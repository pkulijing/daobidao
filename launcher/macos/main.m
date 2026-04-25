/*
 * Daobidao — macOS Native Launcher
 *
 * 极简 Objective-C 二进制，在自身进程内加载 Python 解释器运行
 * daobidao。macOS TCC 权限归属于本二进制（而非 python3），
 * 从而在系统设置中显示 "Daobidao" 而非 "python3.12"。
 *
 * 工作流程：
 *   1. 初始化 NSApplication（让 macOS 认为是正常 app）
 *   2. 读取 venv 配置（~/.config/daobidao/venv-path）
 *   3. 从 pyvenv.cfg 解析 base Python prefix
 *   4. dlopen(libpython3.12.dylib)
 *   5. Py_SetPythonHome → Py_Initialize → 设置 sys.path → 运行 main()
 *
 * 编译：
 *   clang -o daobidao main.m -framework Cocoa -ldl -fobjc-arc
 */

#import <Cocoa/Cocoa.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Python C API 函数签名 */
typedef wchar_t *(*Py_DecodeLocale_t)(const char *, size_t *);
typedef void (*Py_SetPythonHome_t)(const wchar_t *);
typedef void (*Py_SetProgramName_t)(const wchar_t *);
typedef void (*Py_Initialize_t)(void);
typedef int (*Py_IsInitialized_t)(void);
typedef int (*PyRun_SimpleString_t)(const char *);
typedef void (*Py_Finalize_t)(void);
typedef void (*PySys_SetArgvEx_t)(int, wchar_t **, int);

/* ── 工具函数 ─────────────────────────────────────── */

/* 去掉字符串尾部的换行 / 空白 */
static void strip_trailing(char *s) {
    size_t len = strlen(s);
    while (len > 0 && (s[len - 1] == '\n' || s[len - 1] == '\r'
                       || s[len - 1] == ' '))
        s[--len] = '\0';
}

/* 读取文件第一行（去掉换行），失败返回 0 */
static int read_first_line(const char *path, char *buf, size_t bufsz) {
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    if (!fgets(buf, (int)bufsz, f)) { fclose(f); return 0; }
    fclose(f);
    strip_trailing(buf);
    return 1;
}

/*
 * 从 pyvenv.cfg 中解析 "home = /path/to/bin" 行，
 * 返回 base prefix（即 bin 的父目录）。
 */
static int parse_base_prefix(const char *venv, char *out, size_t outsz) {
    char cfg_path[PATH_MAX];
    snprintf(cfg_path, sizeof(cfg_path), "%s/pyvenv.cfg", venv);

    FILE *f = fopen(cfg_path, "r");
    if (!f) return 0;

    char line[1024];
    while (fgets(line, sizeof(line), f)) {
        /* 找 "home = ..." */
        if (strncmp(line, "home", 4) == 0) {
            char *eq = strchr(line, '=');
            if (!eq) continue;
            eq++; /* 跳过 '=' */
            while (*eq == ' ') eq++; /* 跳过空格 */
            strip_trailing(eq);
            /* eq 现在是 "/path/to/bin"，取其父目录 */
            char *last_slash = strrchr(eq, '/');
            if (last_slash) *last_slash = '\0';
            snprintf(out, outsz, "%s", eq);
            fclose(f);
            return 1;
        }
    }
    fclose(f);
    return 0;
}

/* ── 主逻辑 ───────────────────────────────────────── */

static void run_python(int argc, char *argv[]) {
    /* 1. 读取 venv 路径 */
    char config_dir[PATH_MAX];
    const char *home = getenv("HOME");
    if (!home) {
        fprintf(stderr, "[launcher] HOME 未设置\n");
        return;
    }
    snprintf(config_dir, sizeof(config_dir),
             "%s/.config/daobidao/venv-path", home);

    char venv[PATH_MAX];
    if (!read_first_line(config_dir, venv, sizeof(venv))) {
        fprintf(stderr,
                "[launcher] 无法读取 venv 路径: %s\n"
                "[launcher] 请先运行: daobidao --install-app\n",
                config_dir);
        /* 弹对话框提示用户 */
        dispatch_async(dispatch_get_main_queue(), ^{
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"叨逼叨";
            alert.informativeText =
                @"未找到 Python 环境配置。\n"
                 "请在终端运行：daobidao --install-app";
            [alert addButtonWithTitle:@"好"];
            [alert runModal];
            [NSApp terminate:nil];
        });
        return;
    }

    /* 2. 从 pyvenv.cfg 解析 base prefix */
    char base_prefix[PATH_MAX];
    if (!parse_base_prefix(venv, base_prefix, sizeof(base_prefix))) {
        fprintf(stderr, "[launcher] 无法解析 pyvenv.cfg: %s\n", venv);
        return;
    }

    /* 3. dlopen libpython */
    char libpath[PATH_MAX];
    snprintf(libpath, sizeof(libpath),
             "%s/lib/libpython3.12.dylib", base_prefix);

    void *lib = dlopen(libpath, RTLD_LAZY | RTLD_GLOBAL);
    if (!lib) {
        fprintf(stderr, "[launcher] dlopen 失败: %s\n", dlerror());
        return;
    }

    /* 4. 取函数指针 */
    Py_DecodeLocale_t   py_decode    = dlsym(lib, "Py_DecodeLocale");
    Py_SetPythonHome_t  py_set_home  = dlsym(lib, "Py_SetPythonHome");
    Py_SetProgramName_t py_set_name  = dlsym(lib, "Py_SetProgramName");
    Py_Initialize_t     py_init      = dlsym(lib, "Py_Initialize");
    Py_IsInitialized_t  py_is_init   = dlsym(lib, "Py_IsInitialized");
    PyRun_SimpleString_t py_run      = dlsym(lib, "PyRun_SimpleString");
    Py_Finalize_t       py_finalize  = dlsym(lib, "Py_Finalize");
    PySys_SetArgvEx_t   py_setargv   = dlsym(lib, "PySys_SetArgvEx");

    if (!py_decode || !py_set_home || !py_init || !py_run
        || !py_finalize || !py_setargv) {
        fprintf(stderr, "[launcher] dlsym 失败\n");
        return;
    }

    /* 5. 配置并初始化 Python */
    /* .app 启动时没有终端 locale，Python 默认 ASCII，遇中文 print 会崩 */
    setenv("PYTHONIOENCODING", "utf-8", 0);
    setenv("LC_ALL", "en_US.UTF-8", 0);

    wchar_t *whome = py_decode(base_prefix, NULL);
    py_set_home(whome);

    if (py_set_name) {
        wchar_t *wname = py_decode(argv[0], NULL);
        py_set_name(wname);
    }

    py_init();
    if (!py_is_init || !py_is_init()) {
        fprintf(stderr, "[launcher] Py_Initialize 失败\n");
        return;
    }

    /* 传递命令行参数 */
    wchar_t **wargv = calloc(argc, sizeof(wchar_t *));
    for (int i = 0; i < argc; i++)
        wargv[i] = py_decode(argv[i], NULL);
    py_setargv(argc, wargv, 0);  /* 0 = 不修改 sys.path */

    /* 6. 设置环境 + 运行 */
    char code[4096];
    snprintf(code, sizeof(code),
        "import sys, os, site\n"
        "os.environ['_DAOBIDAO_BUNDLE'] = '1'\n"
        "venv = '%s'\n"
        "sp = os.path.join(venv, 'lib', 'python3.12', 'site-packages')\n"
        "site.addsitedir(sp)\n"
        "from daobidao.__main__ import main\n"
        "main()\n",
        venv);

    py_run(code);
    py_finalize();
    free(wargv);
}

int main(int argc, char *argv[]) {
    @autoreleasepool {
        /* 初始化 Cocoa — 让 macOS 知道我们是正常 app */
        [NSApplication sharedApplication];
        /* Accessory: 不显示 Dock 图标，但可以有菜单栏/托盘 */
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];

        /*
         * 直接在主线程运行 Python。
         * pystray 的 icon.run() 会调用 [NSApp run] 启动事件循环。
         * AppKit 要求 UI 操作（NSStatusBar、NSWindow 等）必须在主线程，
         * 所以不能把 Python 放后台线程。
         */
        run_python(argc, argv);
    }
    return 0;
}
