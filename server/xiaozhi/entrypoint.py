#!/usr/bin/env python3
"""Jarvis xiaozhi-server entrypoint.

1. Render the config template with string.Template from the environment:
     $JARVIS_CONFIG_TEMPLATE (/opt/jarvis/config/.config.yaml.tmpl)
       -> $XIAOZHI_DIR/data/.config.yaml (/opt/xiaozhi-esp32-server/data/.config.yaml)
   Every ${VAR} in the template is required and must be non-empty; all missing names are
   reported at once. Values are escaped for YAML double-quoted scalars (the template keeps
   every placeholder inside double quotes) and the result is parsed back to verify it.
   Placeholder or short secrets (XIAOZHI_AUTH_KEY, LITELLM_MASTER_KEY) stop the start unless
   JARVIS_ALLOW_WEAK_SECRETS=1 (local tests only).
2. exec the upstream command (default: python app.py) in $XIAOZHI_DIR.

Secrets are never printed. Python 3.10 compatible (runs in the xiaozhi image).
"""
import os
import re
import string
import sys

DEFAULT_TEMPLATE = "/opt/jarvis/config/.config.yaml.tmpl"
DEFAULT_XIAOZHI_DIR = "/opt/xiaozhi-esp32-server"
DEFAULT_COMMAND = ["python", "app.py"]

# Optional variables: value used when unset or empty.
OPTIONAL_DEFAULTS = {"XIAOZHI_LOG_LEVEL": "INFO", "VAD_MIN_SILENCE_MS": "700"}
# Values that must never be used for a real deployment: refused unless JARVIS_ALLOW_WEAK_SECRETS=1.
PLACEHOLDER_SECRETS = {"change-me", "change-me-long-random", "sk-change-me-long-random", "changeme", "sk-1234"}
SECRET_VARS = ("XIAOZHI_AUTH_KEY", "LITELLM_MASTER_KEY")
MIN_SECRET_LEN = 16
VAD_SILENCE_RANGE = (200, 3000)  # ms; SV5 recommends 600-800
VALID_LOG_LEVELS = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
# upstream config placeholders start with this ideograph ("you"); xiaozhi treats them as unset
UPSTREAM_PLACEHOLDER = "\u4f60"


class RenderError(Exception):
    pass


class MissingVariables(RenderError):
    def __init__(self, names):
        self.names = sorted(set(names))
        super().__init__(
            "missing or empty environment variable(s): " + ", ".join(self.names)
        )


def placeholders(text):
    """Names of all ${VAR} / $VAR placeholders in a string.Template text (Python 3.10 safe)."""
    names = []
    for match in string.Template.pattern.finditer(text):
        if match.group("invalid") is not None:
            line = text.count("\n", 0, match.start("invalid")) + 1
            raise RenderError("invalid '$' at template line %d (write $$ for a literal dollar)" % line)
        name = match.group("named") or match.group("braced")
        if name and name not in names:
            names.append(name)
    return names


def yaml_double_quoted(value):
    """Escape a value for use inside a YAML double-quoted scalar."""
    out = []
    for ch in value:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif code < 0x20 or code == 0x7F or 0x80 <= code < 0xA0:
            out.append("\\x%02x" % code)
        elif code in (0x2028, 0x2029, 0xFEFF):
            out.append("\\u%04x" % code)
        else:
            out.append(ch)
    return "".join(out)


def derived_environment(environ):
    """Copy of environ plus optional defaults and derived values."""
    env = dict(environ)
    for key, value in OPTIONAL_DEFAULTS.items():
        if not env.get(key, "").strip():
            env[key] = value
    env["XIAOZHI_LOG_LEVEL"] = env["XIAOZHI_LOG_LEVEL"].strip().upper()
    env["VAD_MIN_SILENCE_MS"] = env["VAD_MIN_SILENCE_MS"].strip()
    if not env.get("PUBLIC_VISION_URL", "").strip():
        ota = env.get("PUBLIC_OTA_URL", "").strip()
        if ota:
            base = re.sub(r"/xiaozhi/ota/?$", "", ota.rstrip())
            env["PUBLIC_VISION_URL"] = base.rstrip("/") + "/mcp/vision/explain"
    return env


def validate_environment(env, names):
    """Semantic checks on the values used by the template. Returns warnings."""
    errors, warnings = [], []
    ws = env.get("PUBLIC_WS_URL", "")
    if "PUBLIC_WS_URL" in names and ws:
        if not re.match(r"^wss?://[^/\s]+", ws):
            errors.append("PUBLIC_WS_URL must start with ws:// or wss:// (got %r)" % ws)
        if UPSTREAM_PLACEHOLDER in ws:
            errors.append("PUBLIC_WS_URL still contains the upstream placeholder")
    vision = env.get("PUBLIC_VISION_URL", "")
    if "PUBLIC_VISION_URL" in names and vision and not re.match(r"^https?://[^/\s]+", vision):
        errors.append("PUBLIC_VISION_URL (or PUBLIC_OTA_URL) must start with http:// or https://")
    level = env.get("XIAOZHI_LOG_LEVEL", "")
    if "XIAOZHI_LOG_LEVEL" in names and level and level.upper() not in VALID_LOG_LEVELS:
        errors.append("XIAOZHI_LOG_LEVEL must be one of %s" % ", ".join(sorted(VALID_LOG_LEVELS)))
    vad = env.get("VAD_MIN_SILENCE_MS", "")
    if "VAD_MIN_SILENCE_MS" in names and vad:
        low, high = VAD_SILENCE_RANGE
        if not re.match(r"^[0-9]+$", vad) or not low <= int(vad) <= high:
            errors.append("VAD_MIN_SILENCE_MS must be an integer between %d and %d (ms), got %r" % (low, high, vad))
    allow_weak = env.get("JARVIS_ALLOW_WEAK_SECRETS", "").strip() == "1"
    for key in SECRET_VARS:
        value = env.get(key, "")
        if key in names and value:
            if UPSTREAM_PLACEHOLDER in value:
                errors.append("%s must not contain the upstream placeholder text" % key)
            elif value.strip().lower() in PLACEHOLDER_SECRETS or len(value.strip()) < MIN_SECRET_LEN:
                problem = "%s is a published placeholder or shorter than %d characters" % (key, MIN_SECRET_LEN)
                if allow_weak:
                    warnings.append(problem + " (allowed by JARVIS_ALLOW_WEAK_SECRETS=1)")
                else:
                    errors.append(problem + ": generate one (openssl rand -hex 32; LiteLLM: \"sk-\" + hex)"
                                  " or set JARVIS_ALLOW_WEAK_SECRETS=1 for a local test")
    if errors:
        raise RenderError("; ".join(errors))
    return warnings


def render(template_text, environ):
    """Render the template. Returns (text, names, warnings); raises RenderError."""
    names = placeholders(template_text)
    env = derived_environment(environ)
    missing = [n for n in names if not env.get(n, "").strip()]
    if missing:
        # PUBLIC_VISION_URL is normally derived from PUBLIC_OTA_URL: name the variable users set
        raise MissingVariables(
            ["PUBLIC_OTA_URL (or PUBLIC_VISION_URL)" if n == "PUBLIC_VISION_URL" else n for n in missing]
        )
    warnings = validate_environment(env, names)
    mapping = {n: yaml_double_quoted(env[n]) for n in names}
    text = string.Template(template_text).substitute(mapping)
    check_rendered(text, {n: env[n] for n in names})
    return text, names, warnings


def _string_leaves(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            _string_leaves(key, out)
            _string_leaves(value, out)
    elif isinstance(node, list):
        for item in node:
            _string_leaves(item, out)
    elif isinstance(node, str):
        out.append(node)


def check_rendered(text, values):
    """Parse the rendered YAML and make sure every value survived intact."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is in the xiaozhi image
        return
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise RenderError("rendered config is not valid YAML: %s" % e)
    if not isinstance(data, dict) or not data:
        raise RenderError("rendered config is empty")
    leaves = []
    _string_leaves(data, leaves)
    for name, value in values.items():
        if not any(value in leaf for leaf in leaves):
            raise RenderError("value of %s did not survive YAML parsing; keep ${%s} inside double quotes" % (name, name))


def write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def log(message):
    sys.stderr.write("[jarvis-entrypoint] %s\n" % message)
    sys.stderr.flush()


def main(argv=None, environ=None, exec_fn=os.execvp):
    argv = list(sys.argv[1:] if argv is None else argv)
    environ = dict(os.environ if environ is None else environ)
    template_path = environ.get("JARVIS_CONFIG_TEMPLATE", DEFAULT_TEMPLATE)
    xiaozhi_dir = environ.get("XIAOZHI_DIR", DEFAULT_XIAOZHI_DIR)
    output_path = os.path.join(xiaozhi_dir, "data", ".config.yaml")

    if environ.get("JARVIS_SKIP_RENDER", "") == "1":
        log("JARVIS_SKIP_RENDER=1: keeping existing %s" % output_path)
    else:
        try:
            with open(template_path, "r", encoding="utf-8") as fh:
                template_text = fh.read()
            text, names, warnings = render(template_text, environ)
        except MissingVariables as e:
            log("ERROR: cannot render %s" % template_path)
            log("ERROR: missing or empty environment variable(s): %s" % ", ".join(e.names))
            log("Set them in server/.env (see server/.env.example) and recreate the container.")
            return 2
        except (OSError, RenderError) as e:
            log("ERROR: cannot render %s: %s" % (template_path, e))
            return 2
        for warning in warnings:
            log("WARNING: %s" % warning)
        write_atomic(output_path, text)
        env = derived_environment(environ)
        log("rendered %s (%d variables)" % (output_path, len(names)))
        log("websocket url for devices: %s" % env.get("PUBLIC_WS_URL", "?"))

    command = argv or list(DEFAULT_COMMAND)
    os.chdir(xiaozhi_dir)
    sys.stdout.flush()
    exec_fn(command[0], command)
    return 0  # only reached when exec_fn is a test double


if __name__ == "__main__":
    sys.exit(main())
