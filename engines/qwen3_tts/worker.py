"""Proxy PyWorker pour l'autoscaler Vast.

Il ne contient aucune logique : il relaie vers le serveur de modele local et
laisse l'autoscaler decider quand une machine doit exister. La disponibilite
est lue dans le log du serveur, pas devinee.
"""
from vastai import BenchmarkConfig, HandlerConfig, LogActionConfig, Worker, WorkerConfig

MODEL_SERVER_URL = "http://127.0.0.1"
MODEL_SERVER_PORT = 18100
MODEL_LOG_FILE = "/var/log/abo-qwen.log"
MODEL_HEALTHCHECK_ENDPOINT = "/health"

MODEL_LOAD_LOG_MSG = ["ABO_QWEN_READY"]
MODEL_ERROR_LOG_MSGS = [
    "engine failed",
    "CUDA error",
    "no kernel image is available",
    "Traceback (most recent call last)",
]

# L'autoscaler mesure la capacite d'un worker en executant reellement ces
# requetes a son demarrage. Elles sont donc volontairement courtes : ce qu'on
# cherche est un etalon, pas une demonstration.
BENCHMARK_DATASET = [
    {
        "text": phrase,
        "language": "French",
        "preset_voice": "ryan",
    }
    for phrase in (
        "Bonjour, ceci est un essai.",
        "Le vent se leve sur la mer.",
        "Il faut tenter de vivre.",
    )
]

worker_config = WorkerConfig(
    model_server_url=MODEL_SERVER_URL,
    model_server_port=MODEL_SERVER_PORT,
    model_log_file=MODEL_LOG_FILE,
    model_healthcheck_url=MODEL_HEALTHCHECK_ENDPOINT,
    handlers=[
        HandlerConfig(
            route="/synthesize",
            # Le moteur recharge ses poids a chaque appel : deux syntheses en
            # parallele sur une seule carte se genent au lieu d'aller plus vite.
            allow_parallel_requests=False,
            max_queue_time=30.0,
            benchmark_config=BenchmarkConfig(dataset=BENCHMARK_DATASET, runs=1),
            workload_calculator=lambda payload: float(
                max(len(str(payload.get("text", ""))), 1)
            ),
        ),
        HandlerConfig(
            route="/enroll",
            allow_parallel_requests=False,
            max_queue_time=60.0,
            # Un enrolement est plus lourd qu'une synthese et n'arrive qu'une
            # fois par voix : il pese davantage dans la decision d'autoscale.
            workload_calculator=lambda _: 2000.0,
        ),
        HandlerConfig(
            route="/design",
            allow_parallel_requests=False,
            max_queue_time=60.0,
            # Concevoir une voix est rare et lourd : l'autoscaler doit le peser
            # comme tel plutot que comme une synthese de segment.
            workload_calculator=lambda _: 2000.0,
        ),
    ],
    log_action_config=LogActionConfig(
        on_load=MODEL_LOAD_LOG_MSG,
        on_error=MODEL_ERROR_LOG_MSGS,
    ),
)

Worker(worker_config).run()
