"""r12 regression: PDF-related requirements are real, parseable lines."""
from pathlib import Path
from packaging.requirements import Requirement
p=Path(__file__).resolve().parent/"requirements-lock.txt"
lines=[line.strip() for line in p.read_text("utf-8").splitlines()
       if line.strip() and not line.lstrip().startswith("#")]
assert all("\\n" not in line for line in lines), "Literal escaped newline in requirements lock"
items=[Requirement(line) for line in lines]
by_name={r.name.lower():str(r.specifier) for r in items}
for name,version in (("six","1.17.0"),("pyside6","6.12.0"),("reportlab","4.4.4")):
    assert by_name.get(name)==f"=={version}",(name,by_name.get(name))
assert len(items)==len(by_name),"Duplicate requirements"
print(f"PASS {len(items)} individually parseable requirements; ReportLab and PySide6 pinned")
