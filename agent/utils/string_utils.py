import re

from agent.constants import NEWLINE_SEPARATOR_PATTERN, XML_TAG_PATTERN, UNCLOSED_XML_TAG_PATTERN, EMOTE_PATTERN


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

def clean_tag(tag: str) -> str:
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
    prompt = NEWLINE_SEPARATOR_PATTERN.sub(" || ", prompt)
    return prompt

