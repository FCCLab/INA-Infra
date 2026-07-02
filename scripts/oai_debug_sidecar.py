"""Shared netshoot debug sidecar for OAI workload Deployments."""


def debug_sidecar_container(image="docker.io/nicolaka/netshoot"):
    return {
        "name": "debug",
        "image": image,
        "command": ["sleep", "infinity"],
        "securityContext": {
            "capabilities": {"add": ["NET_ADMIN", "NET_RAW"]},
        },
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "200m", "memory": "128Mi"},
        },
    }


def load_debug_sidecar(debug_lib, image):
    namespace = {}
    with open(debug_lib, encoding="utf-8") as handle:
        exec(compile(handle.read(), debug_lib, "exec"), namespace)
    return namespace["debug_sidecar_container"](image)


import re


def patch_operator_utils_py(text):
    pattern = re.compile(
        r"(    if TESTING == 'yes':\n"
        r"        deployment\['spec'\]\['template'\]\['spec'\]\.pop\('initContainers'\)\n)"
        r"\n?"
        r"(    kopf\.adopt\(deployment\)  # includes namespace, name, existing labels)",
    )
    replacement = r"""\1
    if str(os.getenv('DEBUG_SIDECAR', 'no')).lower() in ('yes', 'true', '1'):
        deployment['spec']['template']['spec']['containers'].append({
            "name": "debug",
            "image": os.getenv('DEBUG_SIDECAR_IMAGE', 'nicolaka/netshoot'),
            "command": ["sleep", "infinity"],
            "securityContext": {
                "capabilities": {"add": ["NET_ADMIN", "NET_RAW"]},
            },
            "resources": {
                "requests": {"cpu": "50m", "memory": "64Mi"},
                "limits": {"cpu": "200m", "memory": "128Mi"},
            },
        })

\2"""
    patched, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise ValueError("operator utils.py patch anchor not found")
    return patched
