"""YAML dumper that renders multi-line strings as literal blocks (``|``).

Keeps generated manifests readable in previews (one line per line) instead of
PyYAML's default folded single-quoted scalar that wraps mid-token.
"""

import yaml


class _BlockStringDumper(yaml.SafeDumper):
    pass


def _represent_str_block(dumper: yaml.Dumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockStringDumper.add_representer(str, _represent_str_block)


def dump_block_yaml(obj: dict) -> str:
    return yaml.dump(
        obj,
        Dumper=_BlockStringDumper,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
    )
