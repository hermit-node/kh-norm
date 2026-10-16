from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_routes():
    batch=(ROOT/'Run-Norm-Terminals.bat').read_text('ascii')
    rootbat=(ROOT/'Run-Norm.bat').read_text('utf-8')
    ps1=(ROOT/'norm-ssh.ps1').read_text('utf-8-sig')
    assert 'Run-Norm.bat' in batch and '--service-only' in batch
    assert 'start_operator_consoles.py' in batch
    assert 'start_workspace.py' not in batch
    assert 'if /I "%~1"=="--service-only" goto :backend_boot' in rootbat
    assert "$StartCandidates = @('Run-Norm-Terminals.bat')" in ps1
    start=next(line for line in ps1.splitlines() if 'Start-Process -FilePath $env:ComSpec' in line)
    assert '--service-only' not in start
    print('PASS standalone terminals: no Qt workspace, PS1 calls independent launcher')
if __name__=='__main__':
    test_routes()
