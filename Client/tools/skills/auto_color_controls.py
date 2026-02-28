import maya.cmds as cmds


def _pick_color(name: str) -> int:
    low = name.lower()
    if "_l" in low or low.startswith("l_"):
        return 6
    if "_r" in low or low.startswith("r_"):
        return 13
    return 17


controls = cmds.ls("*_ctrl", type="transform") or []
applied = []
for ctrl in controls:
    shapes = cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []
    color = _pick_color(ctrl)
    for shape in shapes:
        try:
            cmds.setAttr(f"{shape}.overrideEnabled", 1)
            cmds.setAttr(f"{shape}.overrideColor", color)
            applied.append({"control": ctrl, "shape": shape, "color": color})
        except Exception:
            continue

result = {"action": "auto_color_controls", "updated": len(applied), "items": applied}
