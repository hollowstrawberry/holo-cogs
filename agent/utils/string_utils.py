import os
import re
from urllib.parse import urlparse

from agent.constants import NEWLINE_SEPARATOR_PATTERN, XML_TAG_PATTERN, UNCLOSED_XML_TAG_PATTERN, EMPTY


def get_filename(url: str) -> str:
    return os.path.basename(urlparse(url).path)

def add_xml_group(obj: dict, group: list, group_name: str) -> None:
    single_name = group_name[:-1]
    if len(group) == 1:
        obj[single_name] = group[0]
    elif len(group) > 1:
        obj[group_name] = {single_name: group}

def escape_xml(s: str) -> str:
    return s.replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;").replace('"', "&quot;").replace("&", "&amp;")

def undo_xml(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&apos;", "'").replace("&quot;", '"').replace("&amp;", "&")

def fix_truncated_xml(text: str) -> str:
    text = UNCLOSED_XML_TAG_PATTERN.sub("", text)
    stack = []
    for match in XML_TAG_PATTERN.finditer(text):
        is_closing, tag_name, is_self_closing = match.groups()
        if is_self_closing:
            continue
        elif is_closing:
            if stack and stack[-1] == tag_name:
                stack.pop()
        else:
            stack.append(tag_name)
    for tag in reversed(stack):
        text += f"</{tag}>"
    return text

def make_omitted(chars: int = 0) -> str:
    """Placeholder string for truncated text."""
    return f"{EMPTY}INLINE_OMITTED{chars if chars > 0 else ''}{EMPTY}"

def resolve_omitted(text: str) -> str:
    """Replace placeholders from make_omitted with an xml tag."""
    def repl(match: re.Match) -> str:
        return f'<omitted chars="{match.group(1)}" />' if match.group(1) else "<omitted />"
    return re.sub(rf"{EMPTY}INLINE_OMITTED(\d*){EMPTY}", repl, text)

def omitted_length(chars: int = 0):
    """Length of a resolved tag from make_omitted."""
    return len(resolve_omitted(make_omitted(chars)))

def truncate_with_omitted(text: str, max_length: int, keep_ends: bool = False) -> str:
    """Shortens text to about max_length characters, replacing the omitted part with a placeholder."""
    if len(text) <= max_length:
        return text
    tag_length = omitted_length(len(text))  # widest the tag can be
    if len(text) <= tag_length:
        return text
    kept = max(0, max_length - tag_length)
    if keep_ends:
        kept -= kept % 2
    omitted = make_omitted(len(text) - kept)
    if keep_ends:
        return f"{text[:kept // 2]}{omitted}{text[len(text) - kept // 2:]}"
    return text[:kept] + omitted

def clean_booru_tag(tag: str) -> str:
    tag = tag.lower().strip()
    if len(tag) > 3:
        return tag.replace("_", " ").replace("(", "\\(").replace(")", "\\)")
    else:
        return tag

def farenheit_to_celsius(match: re.Match) -> str:
    f = float(match.group(1))
    c = (f - 32) * 5.0/9.0
    return f"{round(c)}°C/{round(f)}°F"

def parse_prompt(prompt: str) -> str:
    """
    Convert a newline-separated prompt into a pipe-separated prompt.
    Meant to properly show regional prompting to the LLM.
    Probably not needed anymore?
    """
    prompt = NEWLINE_SEPARATOR_PATTERN.sub(" || ", prompt)
    return prompt

