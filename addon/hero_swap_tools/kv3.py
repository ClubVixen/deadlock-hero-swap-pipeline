"""Minimal KeyValues3 text reader/writer, enough to edit ModelDoc .vmdl files.

Objects become dicts (key order kept), arrays become lists, and every scalar is kept as its
original text (Raw), so values this module doesn't understand are written back unchanged.
"""
import re


class Raw(str):
    """A scalar exactly as written in the file: 12.5, true, "text", resource_name:"x.vmat", #[ 00 ff ]."""


def q(text):
    """A quoted KV3 string value."""
    return Raw('"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"')


def unq(raw):
    """The text of a quoted value ('"abc"' -> 'abc'); other values come back as they are."""
    s = str(raw)
    if ":" in s and not s.startswith('"'):
        s = s.split(":", 1)[1]
    if s.startswith('"""'):
        return s[3:-3]
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return s


_TOKEN = re.compile(r'''
    (?P<ws>\s+|//[^\n]*|/\*.*?\*/)
  | (?P<punct>[{}\[\]=,])
  | (?P<mstr>(?:[A-Za-z_][\w+]*:)?"""(?:.|\n)*?""")
  | (?P<blob>\#\[[^\]]*\])
  | (?P<str>(?:[A-Za-z_][\w+]*:)?"(?:\\.|[^"\\])*")
  | (?P<word>[^\s{}\[\]=,]+)
''', re.S | re.X)


def _tokens(text):
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"kv3: cannot read at {text[pos:pos + 40]!r}")
        pos = m.end()
        if m.lastgroup != "ws":
            yield m.group()


def loads(text):
    """(header line, root object)."""
    header = ""
    if text.lstrip().startswith("<!--"):
        end = text.index("-->") + 3
        header, text = text[:end].strip(), text[end:]
    toks = list(_tokens(text))
    i = 0

    def value():
        nonlocal i
        t = toks[i]
        i += 1
        if t == "{":
            obj = {}
            while toks[i] != "}":
                key = unq(toks[i])
                assert toks[i + 1] == "=", f"kv3: expected = after {key}"
                i += 2
                obj[key] = value()
                if toks[i] == ",":
                    i += 1
            i += 1
            return obj
        if t == "[":
            arr = []
            while toks[i] != "]":
                arr.append(value())
                if toks[i] == ",":
                    i += 1
            i += 1
            return arr
        return Raw(t)

    return header, value()


def dumps(header, root):
    out = [header + "\n"] if header else []

    def write(v, ind):
        pad = "\t" * ind
        if isinstance(v, dict):
            out.append("\n" + pad + "{\n")
            for k, x in v.items():
                out.append(pad + "\t" + k + " = ")
                write(x, ind + 1)
                out.append("\n")
            out.append(pad + "}")
        elif isinstance(v, list):
            if not v:
                out.append("[  ]")
                return
            simple = all(not isinstance(x, (dict, list)) for x in v)
            if simple and sum(len(x) for x in v) < 60 and not any(str(x).startswith('"') for x in v):
                out.append("[ " + ", ".join(v) + " ]")
                return
            out.append("\n" + pad + "[\n")
            for x in v:
                if isinstance(x, dict):
                    out.append(pad + "\t{\n")
                    for k, y in x.items():
                        out.append(pad + "\t\t" + k + " = ")
                        write(y, ind + 2)
                        out.append("\n")
                    out.append(pad + "\t},\n")
                else:
                    out.append(pad + "\t")
                    write(x, ind + 1)
                    out.append(",\n")
            out.append(pad + "]")
        else:
            out.append(str(v))

    # the root object starts on its own line in ModelDoc files
    out.append("{\n")
    for k, x in root.items():
        out.append("\t" + k + " = ")
        write(x, 1)
        out.append("\n")
    out.append("}\n")
    return "".join(out)


def walk(node):
    """Every dict under node, depth first."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def find(root, cls):
    return [n for n in walk(root) if unq(n.get("_class", "")) == cls]
