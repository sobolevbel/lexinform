import re

MESSAGE_LIMIT = 4096
_TOKENS = re.compile(r"<[^>]+>|&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);|[^<&]+|[<&]")
_TAG = re.compile(r"<(/?)([a-zA-Z][\w-]*)\b[^>]*>")


def utf16_length(text: str) -> int:
    return len(text) + sum(ord(char) > 0xFFFF for char in text)


def split_html(text: str, *, limit: int = MESSAGE_LIMIT) -> tuple[str, ...]:
    if utf16_length(text) <= limit:
        return (text,)
    parts: list[str] = []
    stack: list[tuple[str, str]] = []
    current = ""
    visible = False
    size = 0

    def close() -> str:
        return "".join(f"</{name}>" for name, _ in reversed(stack))

    def flush() -> None:
        nonlocal current, visible, size
        if not visible:
            raise ValueError("HTML markup alone exceeds the message limit")
        parts.append(current + close())
        current = "".join(tag for _, tag in stack)
        visible = False
        size = utf16_length(current)

    for token in _TOKENS.findall(text):
        match = _TAG.fullmatch(token)
        if match is not None:
            closing, name = match.groups()
            next_stack = list(stack)
            if closing:
                if not next_stack or next_stack[-1][0] != name:
                    raise ValueError("Unbalanced Telegram HTML")
                next_stack.pop()
            elif name not in {"br"}:
                next_stack.append((name, token))
            endings = "".join(f"</{item}>" for item, _ in reversed(next_stack))
            if size + utf16_length(token + endings) > limit:
                flush()
            if size + utf16_length(token + endings) > limit:
                raise ValueError("HTML markup exceeds the message limit")
            current += token
            size += utf16_length(token)
            stack = next_stack
            continue
        units = [token] if token.startswith("&") and token.endswith(";") else list(token)
        ending_size = utf16_length(close())
        for unit in units:
            unit_size = utf16_length(unit)
            if size + unit_size + ending_size > limit:
                flush()
            if size + unit_size + ending_size > limit:
                raise ValueError("HTML markup exceeds the message limit")
            current += unit
            size += unit_size
            visible = True
    if stack:
        raise ValueError("Unbalanced Telegram HTML")
    if visible:
        parts.append(current)
    return tuple(parts)
