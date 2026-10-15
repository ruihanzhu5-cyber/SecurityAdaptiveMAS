"""Read-only v0.6 imports, fixed source checks and explicit local paths."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from ..config import ROOT
from ..orchestration.configuration import load_design,project_config


def inspect(config_path):
    design=load_design(config_path)
    lock=json.loads(Path(design["upstream_lock"]).read_text(encoding="utf-8"))
    mismatches=[]
    for name,entry in lock.items():
        for row in entry.get("files",[]):
            original=Path(row["source_path"])
            if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest()!=row["original_sha256"]:
                mismatches.append(str(original))
            if "derived_path" in row:
                derived=ROOT/row["derived_path"]
                if not derived.is_file() or hashlib.sha256(derived.read_bytes()).hexdigest()!=row["derived_sha256"]:
                    mismatches.append(str(derived))
    imports={}
    if design["paths"]["are_root"] is not None:
        sys.path.insert(0,design["paths"]["are_root"])
        module=importlib.import_module("are")
        imports["are"]={"status":"ok" if Path(module.__file__).resolve().is_relative_to(Path(design["paths"]["are_root"])) else "BLOCKED_import_path",
                        "path":module.__file__}
    for name in ("yaml","litellm","security_adaptive_mas.adapters.aorchestra","security_adaptive_mas.adapters.manta_context"):
        try:
            module=importlib.import_module(name)
            imports[name]={"status":"ok","path":module.__file__}
        except ImportError as error:imports[name]={"status":"BLOCKED","error":str(error)}
    from ..adapters.atm_mutation import ATMMutationAdapter
    try:
        from ..trace import TraceLogger
        # Import/hash inspection only: no mutation calls or model requests.
        adapter=ATMMutationAdapter(design["paths"]["atm_root"],lock,TraceLogger(ROOT/"work/doctor_trace.jsonl"))
        imports["atm"]={"status":"ok","path":adapter.cap.__file__}
    except (ImportError,ValueError,TypeError,FileNotFoundError) as error:
        imports["atm"]={"status":"BLOCKED","error":str(error)}
    config=project_config(design)
    data={key:record.path.is_file() and hashlib.sha256(record.path.read_bytes()).hexdigest()==record.sha256 for key,record in config.records.items()}
    return {"python":sys.version.split()[0],"project_root":str(ROOT),"method":design["method"],"paths":design["paths"],
        "api_key_present":bool(os.environ.get("DEEPSEEK_API_KEY")),"model_calls":0,"imports":imports,
        "source_hash_mismatches":mismatches,"selected_data":data,
        "atm_license":"OPEN_missing_upstream_root_LICENSE_local_source_only",
        "legitimate_task_completion":"unknown_no_independent_checker"}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,default=ROOT/"configs/research_v0_6.yaml")
    args=parser.parse_args(argv)
    result=inspect(args.config)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result
