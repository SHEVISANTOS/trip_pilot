from celery import shared_task

from trips.services import generate_plan_for_trip_request


@shared_task
def build_and_persist_plan_task(trip_request_id: int) -> None:
    """Runs the multi-provider plan generation (flights, hotels, attractions,
    visa, eSIM, exchange rate, maps — up to 7 external calls) off the request
    thread. With CELERY_TASK_ALWAYS_EAGER=True (the local-dev default) this
    still executes inline before .delay() returns, so behavior is unchanged
    until a real worker + broker are running.
    """
    generate_plan_for_trip_request(trip_request_id)
