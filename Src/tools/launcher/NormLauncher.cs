using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

internal static class NormLauncher
{
    private const int JobObjectExtendedLimitInformation = 9;
    private const uint JobObjectLimitKillOnJobClose = 0x2000;

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimitInfo
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimitInfo
    {
        public BasicLimitInfo BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint size);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    private const uint CreateSuspended = 0x00000004;
    private const uint CreateNoWindow = 0x08000000;
    private const uint StartfUseStdHandles = 0x00000100;
    private const uint Infinite = 0xFFFFFFFF;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct StartupInfo
    {
        public uint cb;
        [MarshalAs(UnmanagedType.LPWStr)] public string lpReserved;
        [MarshalAs(UnmanagedType.LPWStr)] public string lpDesktop;
        [MarshalAs(UnmanagedType.LPWStr)] public string lpTitle;
        public uint dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars;
        public uint dwFillAttribute, dwFlags;
        public ushort wShowWindow, cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessInfo
    {
        public IntPtr hProcess, hThread;
        public uint dwProcessId, dwThreadId;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CreateProcess(
        string application, StringBuilder command, IntPtr processAttributes,
        IntPtr threadAttributes, bool inheritHandles, uint flags,
        IntPtr environment, string directory, ref StartupInfo startup, out ProcessInfo process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GetStdHandle(int handleType);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GetConsoleWindow();

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint ResumeThread(IntPtr thread);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint WaitForSingleObject(IntPtr process, uint waitMs);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetExitCodeProcess(IntPtr process, out uint code);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateProcess(IntPtr process, uint exitCode);

    private static string Quote(string value)
    {
        var b = new StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value)
        {
            if (ch == '\\') { slashes++; continue; }
            if (ch == '"')
            {
                b.Append('\\', slashes * 2 + 1);
                b.Append('"');
                slashes = 0;
                continue;
            }
            b.Append('\\', slashes);
            slashes = 0;
            b.Append(ch);
        }
        b.Append('\\', slashes * 2);
        b.Append('"');
        return b.ToString();
    }

    private static IntPtr MakeKillOnCloseJob()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        IntPtr buffer = IntPtr.Zero;
        try
        {
            var info = new ExtendedLimitInfo();
            info.BasicLimitInformation.LimitFlags = JobObjectLimitKillOnJobClose;
            int size = Marshal.SizeOf(typeof(ExtendedLimitInfo));
            buffer = Marshal.AllocHGlobal(size);
            Marshal.StructureToPtr(info, buffer, false);
            if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, buffer, (uint)size))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            return job;
        }
        catch
        {
            CloseHandle(job);
            throw;
        }
        finally
        {
            if (buffer != IntPtr.Zero) Marshal.FreeHGlobal(buffer);
        }
    }

    private static int Main(string[] args)
    {
        try
        {
            string core = Path.GetDirectoryName(typeof(NormLauncher).Assembly.Location);
            string root = Path.GetFullPath(Path.Combine(core, ".."));
            string python = Path.Combine(root, ".venv", "Scripts", "python.exe");
            string source = Path.Combine(core, "norm_main.py");
            if (!File.Exists(python))
            {
                Console.Error.WriteLine("Norm launcher: missing " + python);
                return 120;
            }
            if (!File.Exists(source))
            {
                Console.Error.WriteLine("Norm launcher: missing " + source);
                return 121;
            }

            var command = new StringBuilder(Quote(source));
            foreach (string arg in args) command.Append(' ').Append(Quote(arg));
            var commandLine = new StringBuilder(Quote(python) + " " + command.ToString());
            Environment.SetEnvironmentVariable("NORM_ROOT", root);
            var startup = new StartupInfo();
            startup.cb = (uint)Marshal.SizeOf(typeof(StartupInfo));
            startup.dwFlags = StartfUseStdHandles;
            startup.hStdInput = GetStdHandle(-10);
            startup.hStdOutput = GetStdHandle(-11);
            startup.hStdError = GetStdHandle(-12);

            // Child starts suspended, so it cannot escape containment before
            // being assigned to a kill-on-close Job Object.
            uint flags = CreateSuspended;
            if (GetConsoleWindow() == IntPtr.Zero) flags |= CreateNoWindow;
            IntPtr job = MakeKillOnCloseJob();
            try
            {
                ProcessInfo child;
                if (!CreateProcess(python, commandLine, IntPtr.Zero, IntPtr.Zero, true,
                                   flags, IntPtr.Zero, root, ref startup, out child))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Python startup failed");
                try
                {
                    if (!AssignProcessToJobObject(job, child.hProcess))
                    {
                        int code = Marshal.GetLastWin32Error();
                        TerminateProcess(child.hProcess, 123);
                        throw new Win32Exception(code, "Unable to contain Python child process");
                    }
                    if (ResumeThread(child.hThread) == uint.MaxValue)
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "ResumeThread failed");
                    if (WaitForSingleObject(child.hProcess, Infinite) != 0)
                        throw new InvalidOperationException("WaitForSingleObject failed");
                    uint status;
                    if (!GetExitCodeProcess(child.hProcess, out status))
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "GetExitCodeProcess failed");
                    return unchecked((int)status);
                }
                finally
                {
                    CloseHandle(child.hThread);
                    CloseHandle(child.hProcess);
                }
            }
            finally { CloseHandle(job); }
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("Norm launcher: " + e.Message);
            return 122;
        }
    }
}
