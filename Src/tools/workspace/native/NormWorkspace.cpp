// Norm Workspace: native HWND host for the exact consoles opened by Run-Norm.bat.
// Design origin: recovered 2026-10-07 CompositionProbe.
// DWM/DirectComposition is kept as the separate visual experiment; for interactive
// console input this version uses native 1:1 HWND ownership, never text-widget emulation.
#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <windowsx.h>
#include <tlhelp32.h>
#include <shellapi.h>
#include <algorithm>
#include <array>
#include <fstream>
#include <commdlg.h>
#include <set>
#include <string>
#include <vector>

static const wchar_t* MC=L"NormWorkspaceHost", *PC=L"NormWorkspacePane";
constexpr int RAIL=112, PAD=12, HEAD=34, GRIP=24;
constexpr int START=101, RELEASE=102, MINI=103, MAXI=104, EXIT=105, FONTS=106;
constexpr UINT WM_RAISE_PANE=WM_APP+3;
constexpr COLORREF BG=RGB(17,24,35), PANEL=RGB(30,41,56),
   INK=RGB(236,243,250), MUTED=RGB(150,170,192);
struct Pane {
 const wchar_t* name=nullptr; HWND frame=nullptr, hwnd=nullptr, oldParent=nullptr;
 LONG_PTR style=0, exstyle=0; RECT oldRect{}; bool owned=false;
 int x=-99999,y=-99999,w=-1,h=-1,z=0,index=0;
};
struct FontChoice {
 std::wstring face=L"Consolas";
 int size=14;
 std::wstring weight=L"normal";
};
struct App {
 HWND window=nullptr, start=nullptr, release=nullptr, mini=nullptr, maxi=nullptr, exit=nullptr, fonts=nullptr;
 std::array<Pane,3> panes{{{L"Norm Runtime"},{L"Norm Replies"},{L"Norm Prompt"}}};
 std::wstring root, home, cfg, log, status=L"Ready";
 std::set<HWND> initial;
 std::vector<HANDLE> demos;
 HANDLE batch=nullptr, shutdownProcess=nullptr;
  bool quitting=false, exitReady=false; FILETIME originalSignal{};
 HFONT font=nullptr, titleFont=nullptr;
 std::array<FontChoice,3> roleFonts{{{L"Cascadia Mono",13,L"normal"},
                                     {L"Consolas",14,L"medium"},
                                     {L"Cascadia Code",16,L"normal"}}};
 std::wstring uiFontFace=L"Segoe UI";
 std::wstring uiFontWeight=L"normal";
 int uiFontSize=11;
 bool profiledLaunch=true, seedNativeConfig=false;
 int width=1480,height=900, active=0, dragging=-1,zCounter=0;
 bool resizing=false, layoutReady=false; POINT dragOrigin{}; RECT startRect{};
 HWINEVENTHOOK focusHook=nullptr;
 int testResult=0; bool demo=false,test=false,attachOnly=false,started=false,detached=false;
 DWORD launchAt=0;
} a;
static std::wstring Path(const std::wstring& p,const std::wstring& n) {
 return p+(p.empty()||p.back()==L'\\'?L"":L"\\")+n;
}
static FILETIME LastWrite(const std::wstring& path) {
 WIN32_FILE_ATTRIBUTE_DATA data{};
 if(GetFileAttributesExW(path.c_str(),GetFileExInfoStandard,&data))
  return data.ftLastWriteTime;
 return FILETIME{};
}
static std::wstring ShutdownSignal(){
 return Path(Path(a.root,L"state"),L"operator-console-shutdown.json");
}
static bool NewNormalShutdownSignal(){
 const FILETIME current=LastWrite(ShutdownSignal());
 if(!CompareFileTime(&current,&a.originalSignal) || CompareFileTime(&current,&a.originalSignal)<=0)
  return false;
 std::ifstream f(ShutdownSignal(),std::ios::binary);
 if(!f)return false;
 const std::string text((std::istreambuf_iterator<char>(f)),{});
 return text.find("\"normal-service-exit\"")!=std::string::npos;
}
static bool File(const std::wstring& p) {
 DWORD x=GetFileAttributesW(p.c_str()); return x!=INVALID_FILE_ATTRIBUTES&&!(x&FILE_ATTRIBUTE_DIRECTORY);
}
static std::wstring Directory() {
 wchar_t b[32768]{}; GetModuleFileNameW(nullptr,b,32768);
 std::wstring s=b; auto i=s.find_last_of(L"\\/");return i==std::wstring::npos?L".":s.substr(0,i);
}
static std::wstring Label(HWND w){wchar_t b[1024]{};GetWindowTextW(w,b,1024);return b;}
static void Log(const std::wstring& s) {
 std::wofstream f(a.log,std::ios::app);if(f) f<<GetTickCount64()<<L" "<<s<<L"\n";
}
static void Status(const std::wstring& s) {
 a.status=s;Log(s);if(a.window) InvalidateRect(a.window,nullptr,FALSE);
}
static int GetSetting(const std::string& s,const char* name,int def,int min,int max) {
 auto p=s.find("\""+std::string(name)+"\"");
 if(p==std::string::npos || (p=s.find(':',p))==std::string::npos) return def;
 try{return std::clamp(std::stoi(s.substr(p+1)),min,max);}catch(...){return def;}
}

static std::wstring TextSetting(const std::string& s,const std::string& key,
                                const std::wstring& fallback) {
 auto p=s.find("\""+key+"\"");
 if(p==std::string::npos)return fallback;
 p=s.find(':',p+key.size()+2);
 if(p==std::string::npos)return fallback;
 p=s.find('"',p+1);
 if(p==std::string::npos)return fallback;
 std::string raw;bool escaped=false;
 for(++p;p<s.size();++p){
  const char c=s[p];
  if(escaped){raw.push_back(c);escaped=false;continue;}
  if(c=='\\'){escaped=true;continue;}
  if(c=='"')break;
  raw.push_back(c);
 }
 if(raw.empty()||raw.size()>128)return fallback;
 int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,raw.data(),
                             static_cast<int>(raw.size()),nullptr,0);
 if(n<=0)return fallback;
 std::wstring result(n,L'\0');
 MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,raw.data(),
                     static_cast<int>(raw.size()),&result[0],n);
 return result;
}
static std::string JsonText(const std::wstring& v) {
 int n=WideCharToMultiByte(CP_UTF8,0,v.data(),static_cast<int>(v.size()),
                          nullptr,0,nullptr,nullptr);
 std::string text(n,'\0');
 if(n>0)WideCharToMultiByte(CP_UTF8,0,v.data(),static_cast<int>(v.size()),
                           &text[0],n,nullptr,nullptr);
 std::string out;
 for(char ch:text){
  if(ch=='"' || ch=='\\')out+='\\';
  out+=ch;
 }
 return out;
}
static int FontWeight(const std::wstring& weight) {
 if(weight==L"thin")return FW_THIN;
 if(weight==L"light"||weight==L"extra-light")return FW_LIGHT;
 if(weight==L"medium"||weight==L"semi-bold")return FW_SEMIBOLD;
 if(weight==L"bold"||weight==L"extra-bold"||weight==L"black")return FW_BOLD;
 return FW_NORMAL;
}
static void Load() {
 std::ifstream f(a.cfg,std::ios::binary);if(!f)return;
 std::string s((std::istreambuf_iterator<char>(f)),{});
 a.width=GetSetting(s,"width",a.width,960,3800);
 a.height=GetSetting(s,"height",a.height,660,2600);
 const char* prefixes[]={"runtime_","replies_","prompt_"};
 for(int i=0;i<3;++i){
  auto& p=a.panes[i];std::string k=prefixes[i];
  p.x=GetSetting(s,(k+"x").c_str(),-99999,-10000,8000);
  p.y=GetSetting(s,(k+"y").c_str(),-99999,-10000,8000);
  p.w=GetSetting(s,(k+"w").c_str(),-1,260,8000);
  p.h=GetSetting(s,(k+"h").c_str(),-1,150,8000);
  p.z=GetSetting(s,(k+"z").c_str(),i+1,0,100000);
  a.zCounter=std::max(a.zCounter,p.z);
 }
 const char* roles[]={"runtime_","replies_","prompt_"};
 for(int i=0;i<3;++i){
  std::string key=roles[i];
  FontChoice& c=a.roleFonts[i];
  c.face=TextSetting(s,key+"font_face",c.face);
  c.size=GetSetting(s,(key+"font_size").c_str(),c.size,6,48);
  c.weight=TextSetting(s,key+"font_weight",c.weight);
 }
 a.uiFontFace=TextSetting(s,"ui_font_face",a.uiFontFace);
 a.uiFontWeight=TextSetting(s,"ui_font_weight",a.uiFontWeight);
 a.uiFontSize=GetSetting(s,"ui_font_size",a.uiFontSize,7,28);
 a.profiledLaunch=GetSetting(s,"profiled_launch",1,0,1)!=0;
}
static void SeedInitialFontsFromCurrentNorm(){
 // First run only: don't silently overwrite established role font preferences
 // with old native defaults. The native user JSON becomes authoritative later.
 const auto ini=Path(Path(a.root,L"config"),L"settings.ini");
 const wchar_t* prefixes[]={L"runtime_",L"replies_",L"prompt_"};
 wchar_t buf[256]{};
 for(int i=0;i<3;++i){
  auto& font=a.roleFonts[i];
  const std::wstring prefix=prefixes[i];
  GetPrivateProfileStringW(L"workspace",(prefix+L"font_face").c_str(),
                          font.face.c_str(),buf,256,ini.c_str());
  font.face=buf;
  font.size=std::clamp(int(GetPrivateProfileIntW(L"workspace",
              (prefix+L"font_size").c_str(),font.size,ini.c_str())),6,48);
  GetPrivateProfileStringW(L"workspace",(prefix+L"font_weight").c_str(),
                          font.weight.c_str(),buf,256,ini.c_str());
  font.weight=buf;
 }
 GetPrivateProfileStringW(L"workspace",L"ui_font_face",
             a.uiFontFace.c_str(),buf,256,ini.c_str());a.uiFontFace=buf;
 a.uiFontSize=std::clamp(int(GetPrivateProfileIntW(L"workspace",L"ui_font_size",
             a.uiFontSize,ini.c_str())),7,28);
 GetPrivateProfileStringW(L"workspace",L"ui_font_weight",
             a.uiFontWeight.c_str(),buf,256,ini.c_str());a.uiFontWeight=buf;
 GetPrivateProfileStringW(L"workspace",L"profiled_launch",L"true",buf,256,ini.c_str());
 std::wstring enabled=buf;
 std::transform(enabled.begin(),enabled.end(),enabled.begin(),towlower);
 a.profiledLaunch=enabled==L"true"||enabled==L"yes"||enabled==L"on"||enabled==L"1";
}
static void Save() {
 RECT r{};GetWindowRect(a.window,&r);
 if(IsZoomed(a.window)){
  WINDOWPLACEMENT wp{sizeof(wp)};GetWindowPlacement(a.window,&wp);
  r=wp.rcNormalPosition;
 }
 std::ofstream f(a.cfg,std::ios::trunc|std::ios::binary);
 if(!f)return;
 f<<"{\n  \"schema\": 3,\n  \"width\": "<<r.right-r.left
  <<",\n  \"height\": "<<r.bottom-r.top;
 const char* prefixes[]={"runtime_","replies_","prompt_"};
 for(int i=0;i<3;++i){
  const auto& p=a.panes[i];std::string k=prefixes[i];
  f<<",\n  \""<<k<<"x\": "<<p.x<<",\n  \""<<k<<"y\": "<<p.y
   <<",\n  \""<<k<<"w\": "<<p.w<<",\n  \""<<k<<"h\": "<<p.h
   <<",\n  \""<<k<<"z\": "<<p.z;
 }
 for(int i=0;i<3;++i){
  const auto& c=a.roleFonts[i];std::string k=prefixes[i];
  f<<",\n  \""<<k<<"font_face\": \""<<JsonText(c.face)
   <<"\",\n  \""<<k<<"font_size\": "<<c.size
   <<",\n  \""<<k<<"font_weight\": \""<<JsonText(c.weight)<<"\"";
 }
 f<<",\n  \"ui_font_face\": \""<<JsonText(a.uiFontFace)
  <<"\",\n  \"ui_font_size\": "<<a.uiFontSize
  <<",\n  \"ui_font_weight\": \""<<JsonText(a.uiFontWeight)<<"\""
  <<",\n  \"profiled_launch\": "<<(a.profiledLaunch?1:0)
  <<"\n}\n";
}
static void Place(Pane& p, bool repaint=true) {
 if(!p.frame || p.x==-99999 || p.w<0)return;
 SetWindowPos(p.frame,nullptr,p.x,p.y,p.w,p.h,
   SWP_NOZORDER|SWP_NOACTIVATE|(repaint?0:SWP_NOREDRAW));
}
static void Raise(Pane& p) {
 if(!p.frame)return;
 a.active=p.index;p.z=++a.zCounter;
 SetWindowPos(p.frame,HWND_TOP,0,0,0,0,
   SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE);
 for(auto& other:a.panes)InvalidateRect(other.frame,nullptr,FALSE);
}

static void SizeChild(Pane& p){
 if(!p.hwnd || !IsWindow(p.hwnd))return;
 RECT r{};GetClientRect(p.frame,&r);
 SetWindowPos(p.hwnd,HWND_TOP,0,HEAD,std::max(100L,r.right),
              std::max(90L,r.bottom-HEAD-GRIP),
              SWP_NOACTIVATE|SWP_SHOWWINDOW);
}
static bool Own(Pane& p,HWND w){
 if(p.owned||!IsWindow(w)||GetParent(w))return false;
 p.oldParent=GetParent(w);p.style=GetWindowLongPtrW(w,GWL_STYLE);
 p.exstyle=GetWindowLongPtrW(w,GWL_EXSTYLE);GetWindowRect(w,&p.oldRect);
 const auto clean=p.style&~(static_cast<LONG_PTR>(
      WS_POPUP|WS_CAPTION|WS_THICKFRAME|WS_SYSMENU|WS_MINIMIZEBOX|WS_MAXIMIZEBOX));
 SetWindowLongPtrW(w,GWL_STYLE,clean|WS_CHILD|WS_VISIBLE|WS_CLIPSIBLINGS);
 SetWindowLongPtrW(w,GWL_EXSTYLE,p.exstyle&~static_cast<LONG_PTR>(WS_EX_APPWINDOW|WS_EX_WINDOWEDGE));
 SetLastError(0);SetParent(w,p.frame);
 if(GetParent(w)!=p.frame){
  DWORD error=GetLastError();
  SetWindowLongPtrW(w,GWL_STYLE,p.style);SetWindowLongPtrW(w,GWL_EXSTYLE,p.exstyle);
  SetWindowPos(w,nullptr,0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_NOZORDER|SWP_FRAMECHANGED);
  Log(L"Attachment refused: "+std::wstring(p.name)+L" error="+std::to_wstring(error));
  return false;
 }
 p.hwnd=w;p.owned=true;SizeChild(p);InvalidateRect(p.frame,nullptr,FALSE);
 Log(L"Attached "+std::wstring(p.name)+L" HWND="+std::to_wstring((UINT_PTR)w));return true;
}
static void Unown(Pane& p){
 if(!p.owned)return;
 HWND w=p.hwnd;p.hwnd=nullptr;p.owned=false;
 if(IsWindow(w)){
  ShowWindow(w,SW_HIDE);
  SetParent(w,p.oldParent);
  SetWindowLongPtrW(w,GWL_STYLE,p.style);
  SetWindowLongPtrW(w,GWL_EXSTYLE,p.exstyle);
  auto r=p.oldRect;
  SetWindowPos(w,HWND_TOP,r.left,r.top,std::max(320L,r.right-r.left),
               std::max(250L,r.bottom-r.top),SWP_SHOWWINDOW|SWP_FRAMECHANGED);
 }
 InvalidateRect(p.frame,nullptr,FALSE);
}
static int Owned(){int n=0;for(auto& p:a.panes)if(p.owned&&IsWindow(p.hwnd))++n;return n;}
static void ReleaseAll(){
 for(auto& p:a.panes)Unown(p);
 a.detached=true;Status(L"Released to desktop. Norm service and terminal processes are still running.");
}
static BOOL CALLBACK Enum(HWND w,LPARAM v){
 if(!IsWindowVisible(w))return TRUE;
 auto title=Label(w);
 for(auto& p:a.panes)if(title.find(p.name)!=std::wstring::npos){
  reinterpret_cast<std::vector<HWND>*>(v)->push_back(w);break;
 }
 return TRUE;
}
static std::vector<HWND> Windows(){
 std::vector<HWND> result;EnumWindows(Enum,(LPARAM)&result);return result;
}
// An earlier NormWorkspace may already own the operator HWNDs as its children.
// EnumWindows cannot see those reparented HWNDs. Ask that specific, known window
// class to perform its normal RELEASE command first, rather than starting Norm
// again or stealing a child from another process without releasing its bookkeeping.
struct WorkspaceProbe { HWND current; std::vector<HWND> others; };
static BOOL CALLBACK CollectWorkspaces(HWND h,LPARAM ptr){
 auto* probe=reinterpret_cast<WorkspaceProbe*>(ptr);
 if(h==probe->current || !IsWindow(h))return TRUE;
 wchar_t klass[128]{};
 GetClassNameW(h,klass,128);
 if(wcscmp(klass,MC)==0)probe->others.push_back(h);
 return TRUE;
}
static int ReleaseFromOlderWorkspaces(){
 WorkspaceProbe probe{a.window,{}};
 EnumWindows(CollectWorkspaces,(LPARAM)&probe);
 for(HWND other:probe.others){
  DWORD_PTR result=0;
  SetLastError(0);
  auto ok=SendMessageTimeoutW(other,WM_COMMAND,MAKEWPARAM(RELEASE,BN_CLICKED),0,
       SMTO_ABORTIFHUNG|SMTO_BLOCK,1500,&result);
  if(ok)Log(L"Requested safe terminal release from previous Norm Workspace hwnd="+
     std::to_wstring((UINT_PTR)other));
  else Log(L"Old workspace release did not respond. Win32 error="+
     std::to_wstring(GetLastError()));
 }
 return static_cast<int>(probe.others.size());
}
static int Attach(bool allowOld){
 for(auto w:Windows()){
  if(!allowOld&&a.initial.count(w))continue;
  for(auto& p:a.panes)
   if(!p.owned&&Label(w).find(p.name)!=std::wstring::npos){Own(p,w);break;}
 }
 return Owned();
}

static void Layout(){
 if(!a.window) return;
 RECT r{}; GetClientRect(a.window,&r);
 const int cw=std::max(560L,r.right-RAIL-2*PAD);
 const int ch=std::max(410L,r.bottom-2*PAD);
 if(!a.layoutReady){
  auto& p0=a.panes[0];auto& p1=a.panes[1];auto& p2=a.panes[2];
  const int x0=RAIL+PAD,y0=PAD;
  if(p0.x==-99999){p0.x=x0;p0.y=y0;p0.w=cw*63/100;p0.h=ch*65/100;p0.z=1;}
  if(p1.x==-99999){p1.x=x0+cw*43/100;p1.y=y0+24;p1.w=cw*57/100;p1.h=ch*58/100;p1.z=2;}
  if(p2.x==-99999){p2.x=x0+cw*17/100;p2.y=y0+ch*52/100;p2.w=cw*68/100;p2.h=ch*46/100;p2.z=3;}
  for(int i=0;i<3;++i)a.panes[i].index=i;
  a.zCounter=std::max({a.zCounter,p0.z,p1.z,p2.z});
  a.layoutReady=true;
 }
 for(auto& p:a.panes)Place(p);
 std::array<Pane*,3> sorted{{&a.panes[0],&a.panes[1],&a.panes[2]}};
 std::sort(sorted.begin(),sorted.end(),[](const Pane* l,const Pane* r){return l->z<r->z;});
 for(auto* p:sorted)SetWindowPos(p->frame,HWND_TOP,0,0,0,0,
    SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE);
 a.active=sorted[2]->index;
 if(a.start)MoveWindow(a.start,10,88,RAIL-20,40,TRUE);
 if(a.release)MoveWindow(a.release,10,134,RAIL-20,40,TRUE);
 if(a.mini)MoveWindow(a.mini,10,184,RAIL-20,40,TRUE);
 if(a.maxi)MoveWindow(a.maxi,10,230,RAIL-20,40,TRUE);
 if(a.fonts)MoveWindow(a.fonts,10,276,RAIL-20,40,TRUE);
 if(a.exit)MoveWindow(a.exit,10,325,RAIL-20,40,TRUE);
 for(auto& p:a.panes)SizeChild(p);
}
static void CALLBACK FocusChanged(HWINEVENTHOOK,DWORD,HWND hwnd,LONG objectId,LONG,DWORD,DWORD){
 if(objectId!=OBJID_CLIENT || !hwnd || !a.window || !IsWindow(a.window))return;
 for(int i=0;i<3;++i){
  auto& p=a.panes[i];
  if(p.owned && p.hwnd && (hwnd==p.hwnd || IsChild(p.hwnd,hwnd))){
   if(a.active!=i)PostMessageW(a.window,WM_RAISE_PANE,(WPARAM)i,0);
   return;
  }
 }
}
static LRESULT CALLBACK PaneWnd(HWND w,UINT m,WPARAM wp,LPARAM lp){
 auto p=reinterpret_cast<Pane*>(GetWindowLongPtrW(w,GWLP_USERDATA));
 switch(m){
 case WM_NCCREATE:
  SetWindowLongPtrW(w,GWLP_USERDATA,(LONG_PTR)((CREATESTRUCTW*)lp)->lpCreateParams);return TRUE;
 case WM_ERASEBKGND:return 1;
 case WM_SIZE:if(p)SizeChild(*p);return 0;
 case WM_MOUSEACTIVATE:
  if(p && a.active!=p->index)Raise(*p);
  return MA_ACTIVATE;
 case WM_PARENTNOTIFY:
  if(p && LOWORD(wp)==WM_LBUTTONDOWN && a.active!=p->index)Raise(*p);
  return 0;
 case WM_SETCURSOR:{
  if(!p || LOWORD(lp)!=HTCLIENT)break;
  POINT pt{};GetCursorPos(&pt);ScreenToClient(w,&pt);
  RECT r{};GetClientRect(w,&r);
  if(pt.x>r.right-GRIP && pt.y>r.bottom-GRIP){
   SetCursor(LoadCursorW(nullptr,IDC_SIZENWSE));return TRUE;
  }
  if(pt.y<HEAD){
   SetCursor(LoadCursorW(nullptr,IDC_SIZEALL));return TRUE;
  }
  break;
 }
 case WM_LBUTTONDOWN:{
  if(!p)return 0;
  Raise(*p);
  RECT r{};GetClientRect(w,&r);
  int x=GET_X_LPARAM(lp),y=GET_Y_LPARAM(lp);
  bool resize=(x>=r.right-GRIP && y>=r.bottom-GRIP);
  if(!resize && y>=HEAD)return 0;
  a.resizing=resize;a.dragging=p->index;
  GetCursorPos(&a.dragOrigin);
  a.startRect={p->x,p->y,p->w,p->h};
  SetCapture(w);
  return 0;
 }
 case WM_MOUSEMOVE:
  if(p && a.dragging==p->index && GetCapture()==w && (wp&MK_LBUTTON)){
   POINT pt{};GetCursorPos(&pt);
   int dx=pt.x-a.dragOrigin.x,dy=pt.y-a.dragOrigin.y;
   RECT rc{};GetClientRect(a.window,&rc);
   if(a.resizing){
    p->w=std::clamp<int>(a.startRect.right+dx,260,std::max(400L,rc.right-RAIL));
    p->h=std::clamp<int>(a.startRect.bottom+dy,150,std::max(300L,rc.bottom));
   }else{
    p->x=std::clamp<int>(a.startRect.left+dx,RAIL-std::max(0,p->w-92),rc.right-92);
    p->y=std::clamp<int>(a.startRect.top+dy,0,rc.bottom-HEAD);
   }
   Place(*p);
   return 0;
  }
  break;
 case WM_LBUTTONUP:
  if(p && a.dragging==p->index){
   a.dragging=-1;ReleaseCapture();return 0;
  }
  break;
 case WM_CAPTURECHANGED:
  if(p && a.dragging==p->index)a.dragging=-1;
  return 0;
 case WM_PAINT:{
  PAINTSTRUCT ps{};HDC dc=BeginPaint(w,&ps);RECT r{};GetClientRect(w,&r);
  HBRUSH b=CreateSolidBrush(PANEL);FillRect(dc,&r,b);DeleteObject(b);
  const bool hot=p && a.active==p->index;
  RECT head{0,0,r.right,HEAD};
  b=CreateSolidBrush(hot?RGB(22,91,110):RGB(33,47,66));
  FillRect(dc,&head,b);DeleteObject(b);
  SetBkMode(dc,TRANSPARENT);SelectObject(dc,a.titleFont);SetTextColor(dc,INK);
  head.left=12;head.right-=70;
  if(p)DrawTextW(dc,p->name,-1,&head,DT_LEFT|DT_VCENTER|DT_SINGLELINE);
  if(p){
   std::wstring layer=L"z:"+std::to_wstring(p->z);
   RECT rank{r.right-66,0,r.right-12,HEAD};
   SelectObject(dc,a.font);SetTextColor(dc,hot?RGB(139,238,234):MUTED);
   DrawTextW(dc,layer.c_str(),-1,&rank,DT_RIGHT|DT_VCENTER|DT_SINGLELINE);
  }
  if(p&&!p->owned){
   RECT body{16,HEAD+18,r.right-16,r.bottom-24};
   SelectObject(dc,a.font);SetTextColor(dc,MUTED);
   DrawTextW(dc,L"Waiting for the original terminal window...",-1,&body,DT_WORDBREAK);
  }
  RECT footer{0,r.bottom-GRIP,r.right,r.bottom};
  b=CreateSolidBrush(RGB(29,42,58));FillRect(dc,&footer,b);DeleteObject(b);
  const int x=r.right-6,y=r.bottom-6;
  HPEN pen=CreatePen(PS_SOLID,2,hot?RGB(98,233,220):RGB(118,160,182));
  HGDIOBJ old=SelectObject(dc,pen);
  for(int k=0;k<3;++k){
   MoveToEx(dc,x-5-k*6,y,nullptr);LineTo(dc,x,y-5-k*6);
  }
  SelectObject(dc,old);DeleteObject(pen);
  b=CreateSolidBrush(hot?RGB(47,184,178):RGB(70,92,116));
  FrameRect(dc,&r,b);DeleteObject(b);
  EndPaint(w,&ps);return 0;
 }
 }return DefWindowProcW(w,m,wp,lp);
}
static std::wstring Cmd(){
 wchar_t b[32768]{};DWORD n=GetEnvironmentVariableW(L"ComSpec",b,32768);
 return n&&n<32768?b:L"C:\\Windows\\System32\\cmd.exe";
}
static bool Process(std::wstring command,DWORD flags,HANDLE* handle,bool capture=false){
 STARTUPINFOW si{};si.cb=sizeof(si);PROCESS_INFORMATION pi{};
 HANDLE out=INVALID_HANDLE_VALUE, in=INVALID_HANDLE_VALUE;
 SECURITY_ATTRIBUTES sa{sizeof(sa),nullptr,TRUE};
 if(capture){
  out=CreateFileW(Path(a.home,L"NormWorkspace-startup.log").c_str(),
        GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,&sa,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
  in=CreateFileW(L"NUL",GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,&sa,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
  if(out!=INVALID_HANDLE_VALUE && in!=INVALID_HANDLE_VALUE){
   si.dwFlags=STARTF_USESTDHANDLES;
   si.hStdOutput=out;si.hStdError=out;si.hStdInput=in;
  }else{Log(L"Could not redirect Run-Norm.bat output.");capture=false;}
 }
 std::vector<wchar_t> chars(command.begin(),command.end());chars.push_back(0);
 BOOL ok=CreateProcessW(nullptr,chars.data(),nullptr,nullptr,capture,flags,
                    nullptr,a.root.c_str(),&si,&pi);
 if(out!=INVALID_HANDLE_VALUE)CloseHandle(out);
 if(in!=INVALID_HANDLE_VALUE)CloseHandle(in);
 if(!ok){
  Log(L"CreateProcess failed "+std::to_wstring(GetLastError())+L" cmd="+command);return false;
 }
 CloseHandle(pi.hThread);*handle=pi.hProcess;return true;
}
static void Launch(){
 if(a.started)return;
 a.started=true;a.detached=false;a.launchAt=GetTickCount();
 a.initial.clear();for(auto h:Windows())a.initial.insert(h);
 if(a.demo){
  for(auto& pane:a.panes){
   std::wstring c=L"\""+Cmd()+L"\" /D /K \"title "+pane.name+
    L" & echo Native cmd.exe test: "+pane.name+L"\"";
   HANDLE h=nullptr;
   if(Process(c,CREATE_NEW_CONSOLE|CREATE_NEW_PROCESS_GROUP,&h))a.demos.push_back(h);
  }
  Status(L"Demo: three independent cmd.exe terminals launched; acquiring native HWNDs...");
 }else{
  auto bat=Path(a.root,L"Run-Norm.bat");
  if(!File(bat)){Status(L"Run-Norm.bat is missing under: "+a.root);a.detached=true;return;}
     // Use the CURRENT Norm service/role bootstrap, not the legacy v3
   // role script or an additional, independently launched worker generation.
   const auto py=Path(Path(a.root,L".venv"),L"Scripts\\python.exe");
   const auto starter=Path(Path(Path(a.root,L"tools"),L"workspace"),L"native\\start_native_roles.py");
   if(!File(py)||!File(starter)){
    Status(L"Native bootstrap missing. Leave existing Norm service untouched.");
    a.detached=true;return;
   }
   std::wstring cmd=L"\""+py+L"\" -B \""+starter+L"\" --root \""+a.root+
                    L"\" --config \""+a.cfg+L"\"";
if(!Process(cmd,CREATE_NO_WINDOW,&a.batch,true)){
   Status(L"Could not start Norm operator consoles. See NormWorkspace log.");
   a.detached=true;return;
  }
  Status(a.profiledLaunch ? L"Starting Norm with independent terminal fonts...":
       L"Run-Norm.bat started; waiting for Runtime / Replies / Prompt...");
 }
}
// Registry entries are advisory, not ownership: test that their PIDs really
// belong to still-running Python processes before deciding to suppress startup.
// This avoids duplicate Norm Prompt sessions if their Windows Terminal host is
// temporarily hidden, tabbed or still parented by a different workspace.
static int ExistingConsoleHosts(){
 const auto file=Path(Path(a.root,L"state"),L"operator-consoles.json");
 std::ifstream f(file,std::ios::binary);
 if(!f)return 0;
 std::string data((std::istreambuf_iterator<char>(f)),{});
 int count=0;
 for(size_t pos=0;(pos=data.find("\"pid\"",pos))!=std::string::npos;){
  pos=data.find(':',pos+5);
  if(pos==std::string::npos)break;
  try{
   unsigned long pid=std::stoul(data.substr(pos+1));
   HANDLE proc=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,pid);
   if(!proc)continue;
   DWORD status=0;
   wchar_t executable[32768]{};
   DWORD capacity=32768;
   const bool alive=GetExitCodeProcess(proc,&status)&&status==STILL_ACTIVE;
   const bool found=QueryFullProcessImageNameW(proc,0,executable,&capacity)!=0;
   CloseHandle(proc);
   if(alive && found){
    std::wstring name=executable;
    auto slash=name.find_last_of(L"\\/");
    if(slash!=std::wstring::npos)name=name.substr(slash+1);
    for(auto& c:name)c=static_cast<wchar_t>(towlower(c));
    if(name==L"python.exe" || name==L"pythonw.exe")++count;
   }
  }catch(...) {}
 }
 return count;
}
static void Initialize(){
 if(!a.demo){
  // Previous native workspace may already hold all three HWNDs. Release them
  // through its own WM_COMMAND handler, so closing it later cannot affect ours.
  const int oldCount=ReleaseFromOlderWorkspaces();
  const int count=Attach(true);
  if(count==3){
   a.started=true;
   Status(oldCount?L"Adopted three existing Norm windows from the older workspace.":
     L"Attached three existing Norm windows. Service unchanged.");
   return;
  }
  if(a.attachOnly){
   a.detached=true;
   Status(L"Attach-only: "+std::to_wstring(count)+L" console(s) found.");
   return;
  }
  const int recorded=ExistingConsoleHosts();
  // Fail closed for ambiguous existing sessions: never launch a second Prompt.
  if(count>0 || oldCount>0 || recorded>0){
   a.detached=false;
   Status(L"Existing Norm sessions: "+std::to_wstring(count)+
     L"/3 windows, "+std::to_wstring(recorded)+
     L" registered processes. No duplicate Prompt started.");
   return;
  }
 }
 Launch();
}
static void Tick(){
 if(a.quitting){
  if(a.shutdownProcess){
   DWORD code=STILL_ACTIVE;
   if(GetExitCodeProcess(a.shutdownProcess,&code)&&code!=STILL_ACTIVE){
    CloseHandle(a.shutdownProcess);a.shutdownProcess=nullptr;
    if(code==0){
     a.exitReady=true;PostMessageW(a.window,WM_CLOSE,0,0);return;
    }
    a.quitting=false;
    Status(L"Norm shutdown incomplete ("+std::to_wstring(code)+L"). Service unchanged; see Norm logs, retry Quit Norm.");
   }
  }
  return;
 }
 // A service/BAT shutdown also closes this native workspace, without initiating
 // a second service shutdown or touching its reversible trash.
 if(NewNormalShutdownSignal()){
  a.exitReady=true;PostMessageW(a.window,WM_CLOSE,0,0);return;
 }
 if(a.detached)return;
 int n=Attach(!a.started);
 if(a.batch){
  DWORD exitCode=STILL_ACTIVE;
  if(GetExitCodeProcess(a.batch,&exitCode)&&exitCode!=STILL_ACTIVE){
   CloseHandle(a.batch);a.batch=nullptr;
   if(exitCode!=0&&n!=3){
    Status(L"Run-Norm.bat failed (exit "+std::to_wstring(exitCode)+
     L"). See NormWorkspace-startup.log. Existing Norm service was not touched.");
    a.detached=true;return;
   }
  }
 }
 if(n==3){
  if(a.status.find(L"All 3")!=0)Status(L"All 3 actual terminal windows attached. Native keyboard and mouse input.");
 }else if(a.started&&GetTickCount()-a.launchAt>180000){
  Status(L"Found only "+std::to_wstring(n)+L"/3 consoles. Inspect Norm startup, Tailscale and service health.");
  a.detached=true;
 }
 if(a.test&&GetTickCount()-a.launchAt>18000){
  bool pass=(n==3);
  for(auto& p:a.panes) pass=pass&&p.hwnd&&GetParent(p.hwnd)==p.frame;
  Log(pass?L"SELFTEST PASS: three native console HWNDs parented correctly":L"SELFTEST FAIL: one or more consoles missing");
  if(pass){
   auto& f=a.panes[0];auto& r=a.panes[1];
   r.x=f.x+160;r.y=f.y+85;r.w=std::max(280,f.w-100);r.h=std::max(210,f.h-75);
   Place(r);
   RECT rf{},rr{},overlap{};
   GetWindowRect(f.frame,&rf);GetWindowRect(r.frame,&rr);
   const bool overlaps=IntersectRect(&overlap,&rf,&rr)!=0 &&
       (overlap.right-overlap.left)>100 && (overlap.bottom-overlap.top)>80;
   Raise(f);
   const bool front=GetWindow(f.frame,GW_HWNDPREV)==nullptr;
   RECT resized{};GetWindowRect(r.frame,&resized);
   const bool sized=(resized.right-resized.left)==r.w &&
      (resized.bottom-resized.top)==r.h;
   const bool freeform=overlaps&&front&&sized;
   Log(freeform?L"SELFTEST PASS: overlap, resize and explicit z-order raise":
                L"SELFTEST FAIL: overlap/resize/z-order");
   pass=pass&&freeform;
  }
  a.testResult=pass?0:1;
  for(auto& p:a.panes)Unown(p);
  for(auto proc:a.demos){TerminateProcess(proc,0);WaitForSingleObject(proc,2000);}
  DestroyWindow(a.window);
 }
}
static void PaintButton(DRAWITEMSTRUCT* d){
 HDC dc=d->hDC;RECT r=d->rcItem;
 const int id=(int)d->CtlID;
 COLORREF color=id==START?RGB(29,128,140):id==EXIT?RGB(129,54,65):RGB(43,60,79);
 if(d->itemState&ODS_SELECTED)color=RGB(25,97,109);
 HBRUSH brush=CreateSolidBrush(color);
 HPEN pen=CreatePen(PS_SOLID,1,RGB(72,100,127));
 HGDIOBJ oldb=SelectObject(dc,brush),oldp=SelectObject(dc,pen);
 RoundRect(dc,r.left,r.top,r.right,r.bottom,10,10);
 SelectObject(dc,oldb);SelectObject(dc,oldp);DeleteObject(brush);DeleteObject(pen);
 SelectObject(dc,a.font);SetTextColor(dc,INK);SetBkMode(dc,TRANSPARENT);
 const wchar_t* t=id==START?L"Attach":id==RELEASE?L"Release":id==MINI?L"Minimize":id==MAXI?L"Maximize":id==FONTS?L"Fonts":L"Quit Norm";
 DrawTextW(dc,t,-1,&r,DT_CENTER|DT_SINGLELINE|DT_VCENTER);
}
static void DragOuter(HWND hwnd){
 POINT pt{};GetCursorPos(&pt);
 ReleaseCapture();
 SendMessageW(hwnd,WM_NCLBUTTONDOWN,HTCAPTION,MAKELPARAM(pt.x,pt.y));
}
static void EditNativeFonts(){
 HMENU menu=CreatePopupMenu();
 if(!menu)return;
 AppendMenuW(menu,MF_STRING,201,L"Runtime terminal");
 AppendMenuW(menu,MF_STRING,202,L"Replies terminal");
 AppendMenuW(menu,MF_STRING,203,L"Prompt terminal");
 AppendMenuW(menu,MF_SEPARATOR,0,nullptr);
 AppendMenuW(menu,MF_STRING,204,L"Workspace UI");
 RECT button{};GetWindowRect(a.fonts,&button);
 int chosen=TrackPopupMenu(menu,TPM_RETURNCMD|TPM_NONOTIFY,
                           button.right,button.top,0,a.window,nullptr);
 DestroyMenu(menu);
 if(chosen<201||chosen>204)return;
 bool ui=(chosen==204);
 int index=chosen-201;
 std::wstring face=ui?a.uiFontFace:a.roleFonts[index].face;
 int points=ui?a.uiFontSize:a.roleFonts[index].size;
 LOGFONTW logfont{};
 wcsncpy_s(logfont.lfFaceName,LF_FACESIZE,face.c_str(),_TRUNCATE);
 HDC dc=GetDC(a.window);
 int dpi=dc?GetDeviceCaps(dc,LOGPIXELSY):96;
 if(dc)ReleaseDC(a.window,dc);
 logfont.lfHeight=-MulDiv(points,dpi,72);
 logfont.lfWeight=ui?FontWeight(a.uiFontWeight):FontWeight(a.roleFonts[index].weight);
 CHOOSEFONTW choice{};
 choice.lStructSize=sizeof(choice);
 choice.hwndOwner=a.window;
 choice.lpLogFont=&logfont;
 choice.Flags=CF_SCREENFONTS|CF_INITTOLOGFONTSTRUCT|CF_LIMITSIZE;
 choice.nSizeMin=6;choice.nSizeMax=48;
 if(!ChooseFontW(&choice))return;
 std::wstring weight=logfont.lfWeight>=FW_BOLD?L"bold":
                     logfont.lfWeight>=FW_MEDIUM?L"medium":L"normal";
 int size=std::clamp(int((choice.iPointSize+5)/10),6,48);
 if(ui){
  a.uiFontFace=logfont.lfFaceName;
  a.uiFontWeight=weight;a.uiFontSize=size;
  if(a.font)DeleteObject(a.font);
  if(a.titleFont)DeleteObject(a.titleFont);
  a.font=CreateFontW(-MulDiv(size,dpi,72),0,0,0,FontWeight(weight),
    0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,a.uiFontFace.c_str());
  a.titleFont=CreateFontW(-MulDiv(size+1,dpi,72),0,0,0,FW_SEMIBOLD,
    0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,a.uiFontFace.c_str());
  for(HWND button:{a.start,a.release,a.mini,a.maxi,a.fonts,a.exit})
   if(button)InvalidateRect(button,nullptr,TRUE);
 }else{
  auto& c=a.roleFonts[index];
  c.face=logfont.lfFaceName;c.size=size;c.weight=weight;
 }
 Save();
 Status(ui?L"Native workspace UI font applied and saved.":
  L"Native terminal font saved; next new terminal uses it (existing terminal untouched).");
}
static void RequestNormQuit(){
 if(a.quitting)return;
 const auto py=Path(Path(a.root,L".venv"),L"Scripts\\python.exe");
 const auto tool=Path(Path(a.root,L"tools"),L"safe_shutdown_norm.py");
 if(!File(py)||!File(tool)){
  Status(L"Cannot shut down: Norm verified shutdown helper missing.");return;
 }
 const auto cmd=L"\""+py+L"\" -B \""+tool+L"\" --wait-seconds 300";
 HANDLE process=nullptr;
 if(!Process(cmd,CREATE_NO_WINDOW,&process)){
  Status(L"Could not start verified Norm shutdown. Existing service remains running.");
  return;
 }
 a.quitting=true;a.shutdownProcess=process;
 Status(L"Waiting for Norm checkpoint and verified shutdown. Reversible trash preserved.");
}

static LRESULT CALLBACK MainWnd(HWND w,UINT m,WPARAM wp,LPARAM lp){
 switch(m){
 case WM_NCCALCSIZE:
  if(wp)return 0; // remove caption area without losing native resize behavior
  break;
 case WM_NCHITTEST:{
  POINT p{GET_X_LPARAM(lp),GET_Y_LPARAM(lp)};
  RECT r{};GetWindowRect(w,&r);
  const int edge=9;
  bool left=p.x<r.left+edge,right=p.x>=r.right-edge;
  bool top=p.y<r.top+edge,bottom=p.y>=r.bottom-edge;
  if(top&&left)return HTTOPLEFT;
  if(top&&right)return HTTOPRIGHT;
  if(bottom&&left)return HTBOTTOMLEFT;
  if(bottom&&right)return HTBOTTOMRIGHT;
  if(left)return HTLEFT;
  if(right)return HTRIGHT;
  if(top)return HTTOP;
  if(bottom)return HTBOTTOM;
  return HTCLIENT;
 }
 case WM_GETMINMAXINFO:{
  auto* mi=reinterpret_cast<MINMAXINFO*>(lp);
  mi->ptMinTrackSize={760,500};
  MONITORINFO info{sizeof(info)};
  HMONITOR monitor=MonitorFromWindow(w,MONITOR_DEFAULTTONEAREST);
  if(GetMonitorInfoW(monitor,&info)){
   const RECT& work=info.rcWork;const RECT& area=info.rcMonitor;
   mi->ptMaxPosition={work.left-area.left,work.top-area.top};
   mi->ptMaxSize={work.right-work.left,work.bottom-work.top};
  }
  return 0;
 }
 case WM_CREATE:{
  a.window=w;
  const int dpi=GetDpiForWindow(w)?GetDpiForWindow(w):96;
  a.font=CreateFontW(-MulDiv(a.uiFontSize,dpi,72),0,0,0,FontWeight(a.uiFontWeight),
      0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,a.uiFontFace.c_str());
  a.titleFont=CreateFontW(-MulDiv(a.uiFontSize+1,dpi,72),0,0,0,FW_SEMIBOLD,
      0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,a.uiFontFace.c_str());
  for(int i=0;i<3;++i){
   auto& p=a.panes[i];p.index=i;
   p.frame=CreateWindowExW(0,PC,L"",WS_CHILD|WS_VISIBLE|WS_CLIPCHILDREN|WS_CLIPSIBLINGS,
     0,0,100,100,w,nullptr,GetModuleHandleW(nullptr),&p);
  }
  auto make=[w](int id)->HWND{
   return CreateWindowW(L"BUTTON",L"",WS_CHILD|WS_VISIBLE|BS_OWNERDRAW,
     0,0,42,42,w,(HMENU)(INT_PTR)id,GetModuleHandleW(nullptr),nullptr);
  };
  a.start=make(START);a.release=make(RELEASE);
  a.mini=make(MINI);a.maxi=make(MAXI);a.fonts=make(FONTS);a.exit=make(EXIT);
  SetTimer(w,1,500,nullptr);
  PostMessageW(w,WM_APP+1,0,0);
  return 0;
 }
 case WM_APP+1:
   if(a.seedNativeConfig){Layout();Save();a.seedNativeConfig=false;}
   Initialize();return 0;
 case WM_RAISE_PANE:
  if(wp<3 && a.panes[wp].owned)Raise(a.panes[wp]);
  return 0;
 case WM_SIZE:Layout();return 0;
 case WM_COMMAND:
  if(LOWORD(wp)==MINI){ShowWindow(w,SW_MINIMIZE);return 0;}
  if(LOWORD(wp)==MAXI){ShowWindow(w,IsZoomed(w)?SW_RESTORE:SW_MAXIMIZE);return 0;}
  if(LOWORD(wp)==EXIT){RequestNormQuit();return 0;}
   if(LOWORD(wp)==FONTS){EditNativeFonts();return 0;}
  if(LOWORD(wp)==RELEASE){ReleaseAll();return 0;}
  if(LOWORD(wp)==START){
   a.detached=false;
   ReleaseFromOlderWorkspaces();
   if(Attach(true)==3){Status(L"Attached existing Norm terminals.");return 0;}
   if(Owned()>0){
    Status(L"Some consoles already exist; waiting for the missing ones. No duplicate startup.");
    return 0;
   }
   const int recorded=ExistingConsoleHosts();
   if(recorded>0){
    Status(L"Found "+std::to_wstring(recorded)+
      L" live Norm console host processes. Reusing existing sessions only.");
    return 0;
   }
   a.started=false;Launch();return 0;
  }
  break;
 case WM_DRAWITEM: PaintButton(reinterpret_cast<DRAWITEMSTRUCT*>(lp));return TRUE;
 case WM_ERASEBKGND:return 1;
 case WM_PAINT:{
  PAINTSTRUCT ps{};HDC dc=BeginPaint(w,&ps);RECT r{};GetClientRect(w,&r);
  HBRUSH b=CreateSolidBrush(BG);FillRect(dc,&r,b);DeleteObject(b);
  RECT rail{0,0,RAIL,r.bottom};
  b=CreateSolidBrush(RGB(23,34,49));FillRect(dc,&rail,b);DeleteObject(b);
  RECT brand{10,15,RAIL-10,65};
  SetBkMode(dc,TRANSPARENT);SelectObject(dc,a.titleFont);SetTextColor(dc,RGB(89,228,213));
  DrawTextW(dc,L"N",-1,&brand,DT_CENTER|DT_SINGLELINE|DT_VCENTER);
  SelectObject(dc,a.font);SetTextColor(dc,MUTED);
  RECT note{8,r.bottom-86,RAIL-8,r.bottom-44};
  std::wstring count=std::to_wstring(Owned())+L" / 3";
  DrawTextW(dc,count.c_str(),-1,&note,DT_CENTER|DT_VCENTER|DT_SINGLELINE);
  RECT edge{RAIL-1,0,RAIL+1,r.bottom};
  b=CreateSolidBrush(RGB(58,79,101));FillRect(dc,&edge,b);DeleteObject(b);
  b=CreateSolidBrush(RGB(47,70,88));FrameRect(dc,&r,b);DeleteObject(b);
  EndPaint(w,&ps);return 0;
 }
 case WM_TIMER:if(wp==1){Tick();InvalidateRect(w,nullptr,FALSE);}return 0;
 case WM_LBUTTONDOWN:
  // Empty workspace background is a drag target for the captionless outer window.
  DragOuter(w);return 0;
 case WM_CLOSE:
   // The native Quit button invokes verified Norm cleanup first.
   if(!a.exitReady && !a.test){
    RequestNormQuit();return 0;
   }
  Save();
  for(auto& p:a.panes)Unown(p); // real terminal windows survive container closure
  DestroyWindow(w);return 0;
 case WM_DESTROY:
  KillTimer(w,1);
  if(a.focusHook){UnhookWinEvent(a.focusHook);a.focusHook=nullptr;}
  if(a.batch)CloseHandle(a.batch);
   if(a.shutdownProcess)CloseHandle(a.shutdownProcess);
  for(auto proc:a.demos)CloseHandle(proc);
  if(a.font)DeleteObject(a.font);
  if(a.titleFont)DeleteObject(a.titleFont);
  PostQuitMessage(a.testResult);return 0;
 }
 return DefWindowProcW(w,m,wp,lp);
}
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE,PWSTR,int){
 a.home=Directory();a.root=File(Path(a.home,L"Run-Norm.bat"))?a.home:L"C:\\Norm";
 HANDLE mutex=CreateMutexW(nullptr,TRUE,L"Local\\NormWorkspace-Native-05510");
 if(!mutex)return 4;
 if(GetLastError()==ERROR_ALREADY_EXISTS){CloseHandle(mutex);return 0;}
 int argc=0;LPWSTR* argv=CommandLineToArgvW(GetCommandLineW(),&argc);
 for(int i=1;i<argc;++i){
  std::wstring s=argv[i];
  if(s==L"--root"&&i+1<argc)a.root=argv[++i];
  else if(s==L"--demo")a.demo=true;
  else if(s==L"--self-test"){a.demo=true;a.test=true;}
  else if(s==L"--attach-only")a.attachOnly=true;
 }
 LocalFree(argv);
 // A different EXE revision must not silently overwrite the active
 // workspace's JSON layout when both instances are open. Preserve legacy
 // settings as a read-only seed for new instance settings.
 wchar_t ownExe[32768]{};
 GetModuleFileNameW(nullptr,ownExe,32768);
 std::wstring filename=ownExe;
 auto pos=filename.find_last_of(L"\\/");
 if(pos!=std::wstring::npos)filename=filename.substr(pos+1);
 pos=filename.find_last_of(L'.');
 if(pos!=std::wstring::npos)filename=filename.substr(0,pos);
 // Persistent user-scoped native preferences must survive source upgrades.
 wchar_t local[32768]{};
 DWORD localLen=GetEnvironmentVariableW(L"LOCALAPPDATA",local,32768);
 std::wstring preferences=(localLen>0&&localLen<32768)
     ?Path(Path(local,L"Norm"),L"workspace"):a.home;
 CreateDirectoryW(Path(local,L"Norm").c_str(),nullptr);
 CreateDirectoryW(preferences.c_str(),nullptr);
 a.cfg=Path(preferences,L"NormWorkspace-Native.json");
 if(!File(a.cfg)){
   a.seedNativeConfig=true;
  const auto oldSettings=Path(a.home,L"NormWorkspace.json");
  if(File(oldSettings) && _wcsicmp(oldSettings.c_str(),a.cfg.c_str())!=0)
   CopyFileW(oldSettings.c_str(),a.cfg.c_str(),TRUE);
 }
 a.log=Path(a.home,filename+L".log");
 a.originalSignal=LastWrite(ShutdownSignal());
 Load();if(a.seedNativeConfig)SeedInitialFontsFromCurrentNorm();
 Log(L"Started. root="+a.root+(a.demo?L" demo":L" live"));
 WNDCLASSEXW wc{sizeof(wc)};wc.hInstance=instance;wc.lpszClassName=PC;wc.lpfnWndProc=PaneWnd;
 wc.hCursor=LoadCursorW(nullptr,IDC_ARROW);RegisterClassExW(&wc);
 wc.lpszClassName=MC;wc.lpfnWndProc=MainWnd;wc.hIcon=LoadIconW(nullptr,IDI_APPLICATION);
 RegisterClassExW(&wc);
 HWND win=CreateWindowExW(WS_EX_APPWINDOW,MC,L"Norm Workspace â€” Native Windows Console Container",
 WS_POPUP|WS_THICKFRAME|WS_SYSMENU|WS_MINIMIZEBOX|WS_MAXIMIZEBOX|WS_CLIPCHILDREN,CW_USEDEFAULT,CW_USEDEFAULT,a.width,a.height,
 nullptr,nullptr,instance,nullptr);
 if(!win){CloseHandle(mutex);return 3;}
 a.focusHook=SetWinEventHook(EVENT_OBJECT_FOCUS,EVENT_OBJECT_FOCUS,
   nullptr,FocusChanged,0,0,WINEVENT_OUTOFCONTEXT|WINEVENT_SKIPOWNPROCESS);
 if(!a.focusHook)Log(L"Focus event observer not available; title click still raises each pane.");
 ShowWindow(win,a.test?SW_HIDE:SW_SHOW);UpdateWindow(win);
 MSG msg{};while(GetMessageW(&msg,nullptr,0,0)>0){TranslateMessage(&msg);DispatchMessageW(&msg);}
 CloseHandle(mutex);
 return (int)msg.wParam;
}

