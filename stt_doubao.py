"""豆包(火山引擎)语音识别引擎。

使用火山引擎「一句话识别」HTTP API。
文档: https://www.volcengine.com/docs/6561/97816
"""

import base64
import gzip
import json
import uuid
import wave
import io
import websocket


# 火山引擎语音识别 WebSocket 协议常量
PROTOCOL_VERSION = 0b0001
HEADER_SIZE = 0b0001  # 4 * 1 = 4 bytes
MESSAGE_TYPE_FULL_CLIENT_REQUEST = 0b0001
MESSAGE_TYPE_AUDIO_ONLY = 0b0010
MESSAGE_TYPE_FULL_SERVER_RESPONSE = 0b1001
MESSAGE_TYPE_SERVER_ACK = 0b1011
MESSAGE_TYPE_SERVER_ERROR = 0b1111
MESSAGE_SERIALIZATION_JSON = 0b0001
MESSAGE_COMPRESSION_GZIP = 0b0001
MESSAGE_COMPRESSION_NONE = 0b0000


class DoubaoSTT:
    """基于火山引擎语音识别的云端 STT。

    需要在火山引擎控制台创建应用并获取 app_id 和 access_token。
    免费额度: https://www.volcengine.com/docs/6561/163043
    """

    API_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"

    def __init__(self, app_id: str, access_token: str, cluster: str = "volcengine_input_common", language: str = "zh-en"):
        self.app_id = app_id
        self.access_token = access_token
        self.cluster = cluster
        self.language = language

        if not app_id or not access_token:
            print("[doubao] 警告: app_id 或 access_token 未配置，请在 config.yaml 中设置")

    def _build_full_request(self, audio_data: bytes) -> bytes:
        """构建完整的请求消息（包含参数和音频）。"""
        request = {
            "app": {
                "appid": self.app_id,
                "cluster": self.cluster,
                "token": self.access_token,
            },
            "user": {
                "uid": "whisper-input-user",
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "sequence": 1,
                "nbest": 1,
                "show_utterances": False,
            },
            "audio": {
                "format": "wav",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
                "language": self.language,
            },
        }

        payload = json.dumps(request).encode()
        payload = gzip.compress(payload)

        # 构建 header
        header = bytearray()
        # byte 0: protocol_version (4 bits) + header_size (4 bits)
        header.append((PROTOCOL_VERSION << 4) | HEADER_SIZE)
        # byte 1: message_type (4 bits) + message_serialization (4 bits)
        header.append((MESSAGE_TYPE_FULL_CLIENT_REQUEST << 4) | MESSAGE_SERIALIZATION_JSON)
        # byte 2: message_compression (4 bits) + reserved (4 bits)
        header.append((MESSAGE_COMPRESSION_GZIP << 4) | 0x00)
        # byte 3: reserved
        header.append(0x00)
        # payload size (4 bytes, big-endian)
        header.extend(len(payload).to_bytes(4, "big"))

        return bytes(header) + payload

    def _build_audio_message(self, audio_chunk: bytes, is_last: bool = False) -> bytes:
        """构建音频数据消息。"""
        compressed = gzip.compress(audio_chunk)

        header = bytearray()
        header.append((PROTOCOL_VERSION << 4) | HEADER_SIZE)
        header.append((MESSAGE_TYPE_AUDIO_ONLY << 4) | 0x00)
        header.append((MESSAGE_COMPRESSION_GZIP << 4) | 0x00)
        header.append(0x00)
        header.extend(len(compressed).to_bytes(4, "big"))

        return bytes(header) + compressed

    def _parse_response(self, data: bytes) -> str | None:
        """解析服务端响应消息。"""
        if len(data) < 4:
            return None

        msg_type = (data[1] >> 4) & 0x0F
        msg_compression = (data[2] >> 4) & 0x0F

        if msg_type == MESSAGE_TYPE_SERVER_ERROR:
            # 错误响应
            payload = data[8:]
            if msg_compression == MESSAGE_COMPRESSION_GZIP:
                payload = gzip.decompress(payload)
            error = json.loads(payload)
            print(f"[doubao] 服务端错误: {error}")
            return None

        if msg_type in (MESSAGE_TYPE_FULL_SERVER_RESPONSE, MESSAGE_TYPE_SERVER_ACK):
            payload_size = int.from_bytes(data[4:8], "big")
            payload = data[8:8 + payload_size]
            if msg_compression == MESSAGE_COMPRESSION_GZIP:
                payload = gzip.decompress(payload)
            resp = json.loads(payload)

            # 提取识别结果
            if "result" in resp:
                results = resp["result"]
                if isinstance(results, list) and results:
                    return results[0].get("text", "")
                elif isinstance(results, dict):
                    return results.get("text", "")
            # 有些响应格式用 payload_msg
            if "payload_msg" in resp:
                payload_msg = resp["payload_msg"]
                if "result" in payload_msg:
                    results = payload_msg["result"]
                    if isinstance(results, list) and results:
                        return results[0].get("text", "")
            return None

        return None

    def transcribe(self, wav_data: bytes) -> str:
        """将 WAV 音频数据转为文字。

        Args:
            wav_data: 16kHz 16bit 单声道 WAV 格式字节数据

        Returns:
            识别出的文字
        """
        if not wav_data:
            return ""

        if not self.app_id or not self.access_token:
            print("[doubao] 错误: 未配置 app_id 和 access_token")
            return ""

        # 提取 PCM 数据
        buf = io.BytesIO(wav_data)
        with wave.open(buf, "rb") as wf:
            pcm_data = wf.readframes(wf.getnframes())

        if len(pcm_data) < 3200:  # < 0.1s
            return ""

        try:
            ws = websocket.create_connection(
                self.API_URL,
                header=[f"Authorization: Bearer; {self.access_token}"],
                timeout=10,
            )

            # 发送完整请求（含参数）
            full_req = self._build_full_request(pcm_data)
            ws.send(full_req, opcode=websocket.ABNF.OPCODE_BINARY)

            # 分块发送音频
            chunk_size = 32000  # 1秒的音频
            offset = 0
            while offset < len(pcm_data):
                chunk = pcm_data[offset:offset + chunk_size]
                is_last = (offset + chunk_size >= len(pcm_data))
                msg = self._build_audio_message(chunk, is_last)
                ws.send(msg, opcode=websocket.ABNF.OPCODE_BINARY)
                offset += chunk_size

            # 接收结果
            final_text = ""
            while True:
                resp = ws.recv()
                if isinstance(resp, bytes):
                    text = self._parse_response(resp)
                    if text is not None:
                        final_text = text
                    # 检查是否是最终结果
                    msg_type = (resp[1] >> 4) & 0x0F
                    if msg_type == MESSAGE_TYPE_FULL_SERVER_RESPONSE:
                        break
                else:
                    break

            ws.close()
            return final_text

        except Exception as e:
            print(f"[doubao] 识别失败: {e}")
            return ""
