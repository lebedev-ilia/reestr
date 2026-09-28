### cd "/media/ilya/Новый том/TrendFlowML/backend" && ./scripts/stop_e2e_stack.sh --with-infra --quiet || true; pkill -f "celery -A fetcher.celery_app worker" 2>/dev/null || true; sleep 2; ./scripts/start_e2e_stack.sh --with-infra


### curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/docs; echo " 8001"; readlink -f "/media/ilya/Новый том/TrendFlowML/backend/.e2e/logs/latest" 2>/dev/null || echo "no latest"

### source scripts/e2e_env.sh && source .venv/bin/activate && python -u scripts/e2e_full_max_run.py --with-triton-docker --offline-example