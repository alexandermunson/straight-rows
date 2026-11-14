# make reproduce HANDLE=<github-username> REPO=https://github.com/<github-username>/<repo>
PY ?= .venv/bin/python
export PYTHONPATH := src

setup:            ; python3 -m venv .venv && $(PY) -m pip install -r requirements.txt
data:             ; $(PY) scripts/get_data.py
inspect:          ; $(PY) scripts/inspect_data.py --data data
dev:              ; $(PY) -m straightrows.cli dev --data data
compare:          ; $(PY) scripts/compare_baseline.py data
calibrate:        ; $(PY) -m straightrows.calibrate --data data --out outputs/params_calibrated.json --cv
stability:        ; $(PY) scripts/stability.py
test:             ; $(PY) -m unittest discover -s tests -v
gui:              ; $(PY) scripts/viewer.py --refresh
synth:            ; $(PY) -m straightrows.synth --out outputs/synth && $(PY) -m straightrows.synth --out outputs/synth_hard --hard
# the one command that regenerates submission.json from the downloaded data (built-in default thresholds, config.py)
reproduce:
	@test -n "$(HANDLE)" -a -n "$(REPO)" || { echo "usage: make reproduce HANDLE=<github-username> REPO=https://github.com/<github-username>/<repo>"; exit 2; }
	$(PY) -m straightrows.cli run --data data --output submission.json --github-handle $(HANDLE) --repo-url $(REPO)
.PHONY: gui setup data inspect dev compare calibrate stability test synth reproduce
