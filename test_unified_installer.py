
import hashlib, importlib.util, json, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("installer", HERE/"Norm-Installer.py")
m=importlib.util.module_from_spec(spec); import sys; sys.modules["installer"]=m; spec.loader.exec_module(m)

def make_pkg(folder, version, name=None):
    name=name or f"Norm-{version}-portable-source.zip"
    p=folder/name
    manifest={"package_schema":1,"name":"Norm","version":version,"package_type":"portable-source",
              "source_dir":"core","entrypoint":"core/norm_main.py","settings":"config/settings.ini",
              "runtime_config":"config/runtime.json","requirements_lock":"tools/requirements-lock.txt",
              "build_script":"tools/build_norm.py"}
    with zipfile.ZipFile(p,"w") as z:
        z.writestr(f"Norm-{version}/package-manifest.json",json.dumps(manifest))
        z.writestr(f"Norm-{version}/tools/requirements-lock.txt","aiohttp==3.14.3\nredis==8.1.0\n")
        z.writestr(f"Norm-{version}/core/norm_main.py","print('ok')\n")
        z.writestr(f"Norm-{version}/config/settings.ini","[environment]\npython_version=3.14\nvenv_path=.venv\n")
        z.writestr(f"Norm-{version}/config/runtime.json","{}\n")
        z.writestr(f"Norm-{version}/tools/build_norm.py","print('build')\n")
    digest=hashlib.sha256(p.read_bytes()).hexdigest()
    (folder/(p.name+".sha256")).write_text(f"{digest}  {p.name}\n")
    return p

with tempfile.TemporaryDirectory() as td:
    d=Path(td)
    old=make_pkg(d,"0.53.7")
    new=make_pkg(d,"0.53.8")
    # Make older package physically newer: version must still win.
    import os, time
    now=time.time()
    os.utime(new,(now-100,now-100)); os.utime(old,(now,now))
    found=m.find_latest_source(d)
    assert found.name==new.name, (found,new)
    assert m.inspect_package(found).version=="0.53.8"
    m._verify_companion_sha256(found)
    found.write_bytes(found.read_bytes()+b"x")
    try:
        m._verify_companion_sha256(found)
    except m.InstallerError:
        pass
    else:
        raise AssertionError("checksum mismatch was not rejected")

assert "dependency_mode" in m.InstallOptions.__dataclass_fields__
assert m.INSTALLER_VERSION=="1.5.0-unified"
source=(HERE/"Norm-Installer.py").read_text()
assert 'dependency_mode == "package"' in source
assert 'dependency_mode == "newest"' in source
assert '"pip", "freeze", "--all"' in source
assert 'compare_companion_requirements(selected_source)' not in source[source.index("def launch_gui("):]
assert "public-production" not in source
print("PASS: version-first discovery")
print("PASS: manifest inspection")
print("PASS: companion SHA enforcement")
print("PASS: package/newest dependency modes")
print("PASS: newest-mode resolved-environment evidence")
print("PASS: GUI has no sidecar requirements gate")
