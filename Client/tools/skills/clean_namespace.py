import maya.cmds as cmds


def _merge_namespace(ns: str, target: str = ":") -> None:
    if not cmds.namespace(exists=ns):
        return
    if ns in {":", "UI", "shared"}:
        return
    try:
        cmds.namespace(moveNamespace=[ns, target], force=True)
    except Exception:
        pass
    try:
        cmds.namespace(removeNamespace=ns)
    except Exception:
        pass


namespaces = sorted(cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or [], key=len, reverse=True)
for ns in namespaces:
    _merge_namespace(ns)

result = {"action": "clean_namespace", "remaining_namespaces": cmds.namespaceInfo(listOnlyNamespaces=True) or []}
