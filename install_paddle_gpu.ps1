$ErrorActionPreference = "Stop"

$paddlePython = Join-Path $PSScriptRoot ".venv-paddle\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $paddlePython)) {
    throw "PaddleOCR virtual environment not found: $paddlePython"
}

& $paddlePython -m pip uninstall -y paddlepaddle
if ($LASTEXITCODE -ne 0) { throw "Failed to uninstall paddlepaddle (exit $LASTEXITCODE)" }
& $paddlePython -m pip install paddlepaddle-gpu==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
if ($LASTEXITCODE -ne 0) { throw "Failed to install paddlepaddle-gpu (exit $LASTEXITCODE)" }
& $paddlePython -c "import paddle; print('paddle:', paddle.__version__); print('cuda compiled:', paddle.is_compiled_with_cuda()); print('gpu count:', paddle.device.cuda.device_count()); print('device:', paddle.get_device())"
if ($LASTEXITCODE -ne 0) { throw "Paddle GPU verification failed (exit $LASTEXITCODE)" }
& $paddlePython -c "import paddle; paddle.set_device('gpu:0'); x=paddle.randn([1000,1000]); y=paddle.matmul(x,x); print(y.shape); print(paddle.get_device())"
if ($LASTEXITCODE -ne 0) { throw "CUDA tensor verification failed (exit $LASTEXITCODE)" }
