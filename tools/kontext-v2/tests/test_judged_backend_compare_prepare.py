import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "judged_backend_compare_prepare.py"


def load_module():
    spec = importlib.util.spec_from_file_location("judged_backend_compare_prepare", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_backend_compare_prepare_beam_defaults_can_reach_n30(tmp_path: Path):
    module = load_module()

    args = module.build_parser().parse_args(["--output", str(tmp_path / "prepare.json")])

    assert args.beam_size == "1M"
    assert args.beam_offset == 0
    assert args.beam_length >= 2
