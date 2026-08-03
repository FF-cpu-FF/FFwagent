.PHONY: install test lint scan pruefe dashboard api dienst docker sauber

install:
	pip install -r requirements-dev.txt
	pip install -e .

test:
	pytest -v --cov=wohnungsagent --cov-report=term-missing

lint:
	ruff check src tests dashboard
	mypy src

pruefe:            ## Profil und Quellen validieren, ohne zu scrapen
	python -m wohnungsagent.cli pruefe

scan:              ## einmal suchen, ohne KI – verbraucht keine LLM-Tokens
	python -m wohnungsagent.cli scan -v

scan-ki:           ## einmal suchen, mit KI-Zusammenfassungen – verbraucht Tokens
	python -m wohnungsagent.cli scan --mit-ki -v

diagnose:          ## stumme Quelle untersuchen: make diagnose Q=kleinanzeigen
	python -m wohnungsagent.cli diagnose --quelle $(Q)

probelauf:         ## suchen, aber nichts speichern, nichts melden, keine KI
	python -m wohnungsagent.cli scan --dry-run -v

dashboard:
	streamlit run dashboard/app.py

api:
	uvicorn wohnungsagent.api.app:app --reload

dienst:            ## optionaler Dauerbetrieb – nicht der Normalfall
	python -m wohnungsagent.cli dienst

docker:
	docker compose up --build

sauber:
	rm -rf .pytest_cache .ruff_cache .mypy_cache **/__pycache__
