from __future__ import annotations

from typing import Any, Dict, List


def _get(d: Dict[str, Any], *names: str) -> Any:
    for n in names:
        if isinstance(d, dict) and n in d:
            return d[n]
    return None


def normalize_content_to_list(content: Any) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    try:
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    t = item.get("type") or ("text" if isinstance(item.get("text"), str) else None)
                    if t == "text" and isinstance(item.get("text"), str):
                        segments.append({"type": "text", "text": item.get("text")})
                    else:
                        seg: Dict[str, Any] = {}
                        if t:
                            seg["type"] = t
                        if isinstance(item.get("text"), str):
                            seg["text"] = item.get("text")
                        if seg:
                            segments.append(seg)
            return segments
        if isinstance(content, dict):
            if isinstance(content.get("text"), str):
                return [{"type": "text", "text": content.get("text")}]
    except Exception:
        return []
    return []


def segments_to_text(segments: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for seg in segments:
        if isinstance(seg, dict) and seg.get("type") == "text" and isinstance(seg.get("text"), str):
            parts.append(seg.get("text") or "")
    return "".join(parts)


def segments_to_warp_results(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    # 分段处理的配置
    CHUNK_SIZE = 1000  # 每段最大字符数

    def smart_split_text(text: str, chunk_size: int) -> List[str]:
        """智能分割文本，尽量在合适的位置断开"""
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            end = start + chunk_size

            if end >= len(text):
                # 最后一段
                chunks.append(text[start:])
                break

            # 尝试在合适的分割点断开
            split_chars = ['\n\n', '\n', '. ', '。', '！', '？', ', ', '，', ' ']
            best_split = end

            for split_char in split_chars:
                split_pos = text.rfind(split_char, start, end)
                if split_pos > start:
                    best_split = split_pos + len(split_char)
                    break

            chunks.append(text[start:best_split])
            start = best_split

        return chunks

    for seg in segments:
        if isinstance(seg, dict) and seg.get("type") == "text" and isinstance(seg.get("text"), str):
            text = seg.get("text") or ""

            # 如果文本长度超过阈值，进行智能分段处理
            if len(text) > CHUNK_SIZE:
                chunks = smart_split_text(text, CHUNK_SIZE)
                for i, chunk in enumerate(chunks):
                    # 添加分段标记
                    if len(chunks) > 1:
                        if i == 0:
                            chunk += f" [1/{len(chunks)}]"
                        elif i == len(chunks) - 1:
                            chunk = f"[{i+1}/{len(chunks)}] " + chunk
                        else:
                            chunk = f"[{i+1}/{len(chunks)}] " + chunk

                    results.append({"text": {"text": chunk}})
            else:
                # 文本较短，直接添加
                results.append({"text": {"text": text}})

    return results 