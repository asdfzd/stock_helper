"""Read-only public catalog probe; writes selected public fields only, no credentials."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from market_radar.config import CONFIG
from market_radar.providers.kiwoom_rest import KiwoomRest
from market_radar.providers.kiwoom_types import classify_symbol


def main():
    symbols={"AAPL","NVDA","HYG","TQQQ","SOXL","ARBEW","WALDW","BZFDW","SHFSW"}
    result={"received_utc":datetime.now(timezone.utc).isoformat(),"source":"real usa10099","samples":{}}
    rest=KiwoomRest(replace(CONFIG,rest_retries=0),threading.Event())
    try:
        for exchange in ("ND","NY"):
            payload,_,received=rest.request("usa10099",{"stex_tp":exchange})
            for row in payload.get("list",[]):
                if row.get("stk_cd") in symbols:
                    kind,evidence=classify_symbol(row)
                    result["samples"][row["stk_cd"]]={"symbol_type":kind,"evidence":evidence,"received_utc":received}
    except Exception as exc:
        result["error_type"]=type(exc).__name__
    finally:
        rest.close()
    path=ROOT/"data/kiwoom_v2_validation/public_type_probe.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"sample_types":{k:v["symbol_type"] for k,v in result["samples"].items()},"error_type":result.get("error_type")}))
    return bool(result.get("error_type"))


if __name__=="__main__":raise SystemExit(main())
