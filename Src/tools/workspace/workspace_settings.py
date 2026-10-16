"""Norm workspace preferences. Fonts in config/settings.ini; geometry in state."""
from __future__ import annotations
import configparser
import copy
import json
import os
from pathlib import Path
import sys

ROLES=("runtime","replies","prompt")
FONTS={
    "ui":("Segoe UI",10,"normal"),
    "runtime":("Cascadia Mono",13,"normal"),
    "replies":("Consolas",14,"normal"),
    "prompt":("Cascadia Code",16,"normal"),
}
VALID_WEIGHTS={
    "thin","extra-light","light","semi-light","normal","medium",
    "semi-bold","bold","extra-bold","black","extra-black",
}
DEFAULTS={
    "schema":2,
    "norm":{"root":"","startup_script":"Run-Norm.bat",
            "operator_registry":"state/operator-consoles.json",
            "start_if_missing":True,"launch_when_partial":False},
    "capture":{"match":"exact","release_previous_workspace":True,"poll_interval_ms":700,
               "windows":{"runtime":"Norm Runtime","replies":"Norm Replies","prompt":"Norm Prompt"}},
    "workspace":{"x":100,"y":80,"width":1490,"height":880,"maximized":False,
                 "active_role":"prompt",
                 "panes":{"runtime":{"x":120,"y":20,"width":810,"height":480,"z":1},
                          "replies":{"x":690,"y":45,"width":640,"height":400,"z":2},
                          "prompt":{"x":330,"y":420,"width":870,"height":385,"z":3}}},
}
SETTING_DEFAULTS={"enabled":"true","profiled_launch":"true"}
for role,(face,size,weight) in FONTS.items():
    SETTING_DEFAULTS[f"{role}_font_face"]=face
    SETTING_DEFAULTS[f"{role}_font_size"]=str(size)
    SETTING_DEFAULTS[f"{role}_font_weight"]=weight

def merge_dict(defaults,supplied):
    result=copy.deepcopy(defaults)
    for key,value in supplied.items():
        if isinstance(result.get(key),dict) and isinstance(value,dict):
            result[key]=merge_dict(result[key],value)
        else:
            result[key]=value
    return result

def validate_font(role,face,size,weight):
    if role not in FONTS:raise ValueError(f"Unknown font role: {role}")
    if not isinstance(face,str) or not (1<=len(face)<=100) or any(ord(c)<32 for c in face):
        raise ValueError(f"Invalid {role} font family")
    if type(size) is not int or not 6<=size<=48:
        raise ValueError(f"Invalid {role} font size (6..48 pt)")
    if weight not in VALID_WEIGHTS:raise ValueError(f"Invalid {role} font weight")
    return {"face":face,"size":size,"weight":weight}

def parse_fonts(options):
    return {role:validate_font(role,
        options.get(f"{role}_font_face",face),
        int(options.get(f"{role}_font_size",size)),
        options.get(f"{role}_font_weight",weight))
        for role,(face,size,weight) in FONTS.items()}

def patch_workspace_section(text,updates):
    """Modify only keys owned by [workspace], retaining unrelated INI text."""
    newline="\r\n" if "\r\n" in text else "\n"
    rows=text.splitlines(keepends=True)
    first=next((i for i,line in enumerate(rows)
                if line.strip().lower()=="[workspace]"),None)
    if first is None:
        rows.extend([newline if text and not text.endswith(("\n","\r")) else "",
                     newline,"[workspace]"+newline])
        first=len(rows)-1
    end=next((i for i in range(first+1,len(rows))
              if rows[i].lstrip().startswith("[") and "]" in rows[i]),len(rows))
    segment=list(rows[first+1:end])
    for key,value in updates.items():
        if any(c in str(value) for c in "\r\n"):
            raise ValueError("No linebreaks allowed in font preferences")
        location=next((i for i,row in enumerate(segment)
                       if row.split("=",1)[0].strip().lower()==key.lower() and "=" in row),None)
        row=f"{key} = {value}"+newline
        if location is None:
            segment.append(row)
        else:
            segment[location]=row
    rows[first+1:end]=segment
    return "".join(rows)

def persist_ini_workspace(path,updates):
    path=Path(path)
    original=path.read_text(encoding="utf-8-sig")
    new=patch_workspace_section(original,updates)
    parser=configparser.ConfigParser(interpolation=None)
    parser.read_string(new)
    assert parser.has_section("workspace")
    pending=path.with_name(path.name+".workspace-writing")
    with pending.open("x",encoding="utf-8",newline="") as output:
        output.write(new)
        output.flush();os.fsync(output.fileno())
    try:os.replace(pending,path)
    finally:pending.unlink(missing_ok=True)

class Settings:
    def __init__(self,root=None):
        self.root=Path(root or Path(__file__).resolve().parents[2]).resolve()
        self.config_path=self.root/"config/settings.ini"
        cfg=configparser.ConfigParser(interpolation=None)
        if not cfg.read(self.config_path,encoding="utf-8-sig"):
            raise FileNotFoundError(self.config_path)
        self.options=dict(SETTING_DEFAULTS)
        if cfg.has_section("workspace"):self.options.update(dict(cfg.items("workspace")))
        self.fonts=parse_fonts(self.options)
        sys.path.insert(0,str(self.root/"core"))
        from norm_runtime.settings import load_path_settings
        paths=load_path_settings(self.root)
        self.path=paths["state_root"]/"workspace-layout.json"
        self.path.parent.mkdir(parents=True,exist_ok=True)
        data=copy.deepcopy(DEFAULTS)
        data["norm"]["root"]=str(self.root)
        if self.path.is_file():
            state=json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(state,dict):raise ValueError("Invalid workspace layout")
            self.data=merge_dict(data,state)
        else:
            old=Path.home()/"Documents/NormWorkspace-Qt-Focus/NormWorkspace-Qt.json"
            if old.is_file():
                migrated=json.loads(old.read_text(encoding="utf-8-sig"))
                if isinstance(migrated,dict):
                    data["workspace"]=merge_dict(data["workspace"],migrated.get("workspace",{}))
            self.data=data
            self.save()
        self.data["norm"]["root"]=str(self.root)
    def norm_root(self):return self.root
    def registry_path(self):
        return self.root/str(self.data["norm"]["operator_registry"]).replace("\\","/")
    def save(self):
        doc=json.dumps(self.data,indent=2,ensure_ascii=False)+"\n"
        pending=self.path.with_name(self.path.name+".writing")
        pending.write_text(doc,encoding="utf-8")
        os.replace(pending,self.path)
    def set_fonts(self,fonts):
        validated={role:validate_font(role,**cfg) for role,cfg in fonts.items()}
        updates={}
        for role,spec in validated.items():
            updates.update({f"{role}_font_face":spec["face"],
                            f"{role}_font_size":str(spec["size"]),
                            f"{role}_font_weight":spec["weight"]})
        persist_ini_workspace(self.config_path,updates)
        self.options.update(updates)
        self.fonts.update(validated)
    def profiled(self):
        return self.options.get("profiled_launch","true").strip().lower() in ("1","true","yes","on")
