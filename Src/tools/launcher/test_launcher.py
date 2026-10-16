from pathlib import Path
import base64
import subprocess
import tempfile
import time
import unittest

from build_launcher import build, CSC

MOCK_PYTHON = r"""using System;
using System.Diagnostics;
using System.Text;
using System.Threading;

class PythonMock
{
    static int Main(string[] args)
    {
        Console.WriteLine("ROOT=" + Environment.GetEnvironmentVariable("NORM_ROOT"));
        Console.WriteLine("CWD=" + Environment.CurrentDirectory);
        Console.WriteLine("PID=" + Process.GetCurrentProcess().Id);
        for (int i=0; i<args.Length; i++)
            Console.WriteLine("ARG_" + i + "=" +
                Convert.ToBase64String(Encoding.UTF8.GetBytes(args[i])));
        Console.Out.Flush();
        foreach(string a in args)
            if(a == "--sleep") Thread.Sleep(30000);
        return 37;
    }
}
"""

def compile_mock(output):
    proc=subprocess.run([str(CSC),"/nologo","/optimize+","/target:exe",
            "/platform:x64","/out:"+str(output), str(output.parent/"mock.cs")],
            capture_output=True,text=True,errors="replace")
    if proc.returncode:
        raise AssertionError("Mock build failed: "+proc.stdout+proc.stderr)

class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher=build()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="norm-launcher-test-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/"a runtime with spaces"
        (self.root/"core").mkdir(parents=True)
        (self.root/".venv/Scripts").mkdir(parents=True)
        (self.root/"core/norm.exe").write_bytes(self.launcher.read_bytes())
        (self.root/"core/norm_main.py").write_text("# mocked entrypoint\n",encoding="utf-8")
        (self.root/".venv/Scripts/mock.cs").write_text(MOCK_PYTHON,encoding="utf-8",newline="\n")
        compile_mock(self.root/".venv/Scripts/python.exe")

    @property
    def launcher_path(self):
        return self.root/"core/norm.exe"

    def parse(self,output):
        result={}
        for line in output.splitlines():
            if "=" in line:
                a,b=line.split("=",1);result[a]=b
        return result

    def test_arguments_cwd_env_exit_code(self):
        args=["--version","one two",r"trailing\\",'embedded"quote',"", "--service"]
        cp=subprocess.run([str(self.launcher_path),*args],
            cwd=Path(self.temp.name),capture_output=True,text=True,timeout=20)
        self.assertEqual(cp.returncode,37,cp.stderr)
        data=self.parse(cp.stdout)
        self.assertEqual(Path(data["ROOT"]),self.root)
        self.assertEqual(Path(data["CWD"]),self.root)
        got=[base64.b64decode(data["ARG_"+str(i)]).decode("utf-8")
             for i in range(len(args)+1)]
        self.assertEqual(got[0],str(self.root/"core/norm_main.py"))
        self.assertEqual(got[1:],args)

    def test_missing_python_fails_without_fallback(self):
        (self.root/".venv/Scripts/python.exe").unlink()
        cp=subprocess.run([str(self.launcher_path)],capture_output=True,text=True,timeout=10)
        self.assertEqual(cp.returncode,120)
        self.assertIn("missing",cp.stderr.lower())

    def test_missing_source_fails_without_fallback(self):
        (self.root/"core/norm_main.py").unlink()
        cp=subprocess.run([str(self.launcher_path)],capture_output=True,text=True,timeout=10)
        self.assertEqual(cp.returncode,121)
        self.assertIn("missing",cp.stderr.lower())

    def test_terminating_launcher_terminates_python_child(self):
        proc=subprocess.Popen([str(self.launcher_path),"--sleep"],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        try:
            lines=[proc.stdout.readline().strip() for _ in range(3)]
            self.assertTrue(any(x.startswith("PID=") for x in lines),lines)
            pid=int(next(x[4:] for x in lines if x.startswith("PID=")))
            proc.terminate()
            proc.wait(timeout=10)
            import ctypes
            k=ctypes.WinDLL("kernel32",use_last_error=True)
            k.OpenProcess.argtypes=[ctypes.c_uint,ctypes.c_int,ctypes.c_uint]
            k.OpenProcess.restype=ctypes.c_void_p
            k.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint]
            k.WaitForSingleObject.restype=ctypes.c_uint
            k.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint)]
            k.GetExitCodeProcess.restype=ctypes.c_int
            k.CloseHandle.argtypes=[ctypes.c_void_p]
            k.CloseHandle.restype=ctypes.c_int
            done=False
            for _ in range(50):
                h=k.OpenProcess(0x00101000,False,pid)
                if not h:
                    done=True
                    break
                try:
                    code=ctypes.c_uint()
                    if k.WaitForSingleObject(h,5000)==0:
                        done=True
                        break
                finally:
                    k.CloseHandle(h)
                time.sleep(.1)
            self.assertTrue(done,"Python child survived launcher termination")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            proc.stdout.close()
            proc.stderr.close()

if __name__=="__main__":
    unittest.main(verbosity=2)
