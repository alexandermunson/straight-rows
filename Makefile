# make reproduce HANDLE=your-github-username REPO=https://github.com/your-github-username/straight-rows
PY ?= .venv/bin/python
export PYTHONPATH := src
HANDLE ?= your-github-username
REPO ?= https://github.com/your-github-username/straight-rows

setup:            ; python3 -m venv .venv && $(PY) -m pip install -r requirements.txt
data:             ; $(PY) scripts/get_data.py
inspect:          ; $(PY) scripts/inspect_data.py --data data
dev:              ; $(PY) -m straightrows.cli dev --data data --params params.json
compare:          ; $(PY) scripts/compare_baseline.py data params.json
calibrate:        ; $(PY) -m straightrows.calibrate --data data --out params.json --cv
test:             ; $(PY) -m unittest discover -s tests -v
synth:            ; $(PY) -m straightrows.synth --out outputs/synth && $(PY) -m straightrows.synth --out outputs/synth_hard --hard
# the one command that regenerates submission.json from the downloaded data + committed params.json
reproduce:        ; $(PY) -m straightrows.cli run --data data --params params.json --output submission.json --github-handle $(HANDLE) --repo-url $(REPO)
.PHONY: setup data inspect dev compare calibrate test synth reproduce
