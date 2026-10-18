# Builds bitnet.cpp (llama-server with BitNet i2_s kernels) on Windows with VS 2022 Build Tools + ClangCL.
# Mirrors BitNet's setup_env.py for BitNet-b1.58-2B-4T on x86_64, minus the model download (already done).
$ErrorActionPreference = 'Stop'
$vs = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools'
Import-Module "$vs\Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
Enter-VsDevShell -VsInstallPath $vs -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
Set-Location "$PSScriptRoot\BitNet"
python -I utils\codegen_tl2.py --model bitnet_b1_58-3B --BM 160,320,320 --BK 96,96,96 --bm 32,32,32
if ($LASTEXITCODE) { throw 'codegen failed' }
# Static: on Windows a DLL cannot leave quantize_i2_s (defined in ggml-cpu) unresolved in ggml-base.
cmake -B build -DBUILD_SHARED_LIBS=OFF -DBITNET_X86_TL2=OFF -T ClangCL -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_EXAMPLES=ON -DLLAMA_BUILD_COMMON=ON -DLLAMA_BUILD_SERVER=ON
if ($LASTEXITCODE) { throw 'cmake configure failed' }
cmake --build build --config Release -j 8
if ($LASTEXITCODE) { throw 'build failed' }
Get-ChildItem build -Recurse -Filter llama-server.exe | Select-Object -ExpandProperty FullName
