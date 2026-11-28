"""Rebuild the optional offline ITM-LUT model pack from its pinned author files."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import types
import zipfile

import onnxruntime as ort
import torch


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(upstream, header, output):
    expected = {
        "params.pth": "13633e549c04424c066e45da62fddbb45b0018d40eb41d2172869ed3f3d9242d",
        "network.py": "77dd9ee8368e5496b5d06aac661d15df9f55f1c7d5dca73114416219b85647f8",
    }
    for name, sha in expected.items():
        if digest(upstream / name) != sha:
            raise ValueError(f"Unexpected author file: {name}")
    if digest(header) != "573a117ae6b83ead7f53da6cedaa0d7b7d9cdb45c0ba4726bac9081e67696a82" or ort.__version__ != "1.20.1":
        raise ValueError("Requires official ORT 1.20.1 C header and Windows CPU runtime.")
    output.mkdir(parents=True, exist_ok=True)
    stage = output / "model"
    stage.mkdir(exist_ok=True)
    # Export just the pooled CNNs. Spatially uniform LUTs are reconstructed
    # by the production shader, avoiding the native training-time extension.
    ailut = types.ModuleType("ailut")
    ailut.ailut_transform = None
    sys.modules["ailut"] = ailut
    sys.path.insert(0, str(upstream))
    from network import LutNet

    net = LutNet().eval()
    net.load_state_dict(torch.load(upstream / "params.pth", map_location="cpu", weights_only=True))
    class Encoder(torch.nn.Module):
        def __init__(self, model):
            super().__init__()
            self.branches = torch.nn.ModuleList([
                torch.nn.Sequential(getattr(model, "encoder_img_" + b), getattr(model, "lut_gen_" + b).weights_gen)
                for b in ("b", "m", "d")])
        def forward(self, prior):
            return torch.stack([torch.sigmoid(branch(prior)) for branch in self.branches], dim=1)

    encoder = Encoder(net).eval()
    with torch.no_grad():
        torch.onnx.export(encoder, torch.zeros(1, 3, 64, 64), stage / "cinema.onnx", opset_version=17,
                          input_names=["prior"], output_names=["weights"], dynamo=False, external_data=False,
                          dynamic_axes={"prior": {2:"pooled_height", 3:"pooled_width"}})
        bases = torch.stack([getattr(net, "lut_gen_" + b).basic_luts for b in ("b","m","d")])
        bases.detach().cpu().numpy().astype("<f4").tofile(stage / "bases.f32")
    worker = Path(__file__).with_name("cinema_worker.cpp")
    subprocess.run(["g++", "-std=c++17", "-O2", "-ffp-contract=off", "-D_stdcall=__stdcall",
                    "-D_Frees_ptr_opt_=", "-municode", "-static", "-s","-Wl,--no-insert-timestamp",
                    "-I" + str(header.parent), worker, "-o",stage / "cinema-worker.exe"],check=True)
    runtime = Path(ort.__file__).parent
    for name in ("onnxruntime.dll", "onnxruntime_providers_shared.dll"):
        shutil.copyfile(runtime/"capi"/name, stage/name)
    for src, name in ((runtime/"LICENSE", "ONNXRuntime-LICENSE.txt"),(runtime/"ThirdPartyNotices.txt", "ONNXRuntime-notices.txt"),
                      (upstream/"LICENSE","ITM-LUT-MPL-2.0.txt"),(worker,"cinema_worker.cpp")):
        shutil.copyfile(src,stage/name)
    (stage/"SOURCES.txt").write_text(
        "ITM-LUT (2023), Cheng Guo, Leidong Fan, Qian Zhang, Hanyuan Liu, Kanglin Liu, Xiuhua Jiang.\n"
        "Author checkpoint params.pth and network.py at commit 05efa83199a08649a2f3f3355d94e2cee624f774.\n"
        "https://github.com/AndreGuo/ITMLUT/tree/05efa83199a08649a2f3f3355d94e2cee624f774\n"
        "This pack exports the evaluation-mode weight predictors to ONNX and the trained LUT bases to raw float32.\n"
        "The author source files and checkpoint are available from the link above; see ITM-LUT-MPL-2.0.txt.\n"
        "ONNX Runtime 1.20.1 CPU, Microsoft. https://github.com/microsoft/onnxruntime/tree/v1.20.1\n"
        "Runtime from onnxruntime 1.20.1 Windows x64 CPU wheel. See license and notices.\n"
        "cinema_worker.cpp uses the public v1.20.1 onnxruntime_c_api.h; built for Windows x64 with MinGW GCC.\n",
        encoding="utf-8",newline="\n")
    files = sorted(f for f in stage.iterdir() if f.is_file())
    with zipfile.ZipFile(output/"LumaFrame-Cinema-LUT-v1.zip","w",zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(file, file.name)
    (output / "fingerprints.json").write_text(json.dumps(
        {f.name:[f.stat().st_size,digest(f)] for f in files},indent=2),encoding="ascii")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream",type=Path)
    parser.add_argument("ort_c_header",type=Path)
    parser.add_argument("output",type=Path)
    args = parser.parse_args()
    build(args.upstream.resolve(),args.ort_c_header.resolve(),args.output.resolve())
