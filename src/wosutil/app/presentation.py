"""Pure formatting helpers for the instance list.

These functions do not touch Tkinter so they can be tested without a GUI and
reused by any front-end.
"""

from wosutil.tool.tool_instances_controller import TASK_GROUPING_WINDOW_SECONDS, pick_scheduled_task


def format_time_remaining(seconds):
    """Format remaining time in HH:MM:SS format."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def get_next_task_info(state, now):
    """Get the task that will actually run next, mirroring the worker.

    The controller executes the highest-priority task that is already due,
    waiting for a higher-priority task scheduled within the grouping window
    (TASK_GROUPING_WINDOW_SECONDS) first, and waits for the earliest future
    task otherwise. While a picked task is not due yet, the worker keeps
    waiting and re-picking, so a higher-priority in-window task can jump
    ahead. This helper walks that wait chain to find the task the worker will
    actually execute first.

    Args:
        state: The instance state (or None).
        now: Current epoch time.

    Returns:
        tuple: (task name, execution timestamp) or (None, None).
    """
    tasks = getattr(state, "tasks", None) if state else None
    if not tasks:
        return None, None

    sim_now = now
    while True:
        task, wait_until = pick_scheduled_task(tasks, sim_now)
        if task is None:
            return None, None
        if wait_until is None:
            task_name = task.get("name", "?")
            # The task is due now (or at the end of the wait chain)
            return task_name, (None if sim_now == now else sim_now)
        sim_now = wait_until


def get_all_tasks_info(state, now):
    """Get every scheduled task of an instance in the real execution order.

    Tasks whose run times fall within the same window
    (TASK_GROUPING_WINDOW_SECONDS) run grouped by priority, since the worker
    waits for the highest-priority in-window task instead of the one whose
    timer was read earlier. This helper groups the tasks the same way and
    lists each window in priority order, so the preview matches the order in
    which the tasks will actually run; tasks in different windows keep their
    chronological order.

    Args:
        state: The instance state (or None).
        now: Current epoch time.

    Returns:
        List of (task name, remaining seconds) pairs in execution order;
        tasks that are already due have a remaining time of 0.
    """
    tasks = getattr(state, "tasks", None) if state else None
    if not tasks:
        return []

    ordered = sorted(tasks, key=lambda task: (task.get("next_run_time", 0), task.get("priority", 99)))

    clusters = []
    cluster = []
    anchor = None
    for task in ordered:
        task_time = task.get("next_run_time", 0)
        if cluster and task_time > anchor + TASK_GROUPING_WINDOW_SECONDS:
            clusters.append(cluster)
            cluster = []
            anchor = None
        if not cluster:
            anchor = task_time
        cluster.append(task)
    if cluster:
        clusters.append(cluster)

    infos = []
    for cluster in clusters:
        cluster.sort(key=lambda task: (task.get("priority", 99), task.get("next_run_time", 0)))
        for task in cluster:
            remaining = max(0, int(task.get("next_run_time", 0) - now))
            infos.append((task.get("name", "?"), remaining))
    return infos


def format_task_tooltip(state, now):
    """Build the tooltip text with the countdown of every scheduled task.

    Args:
        state: The instance state (or None).
        now: Current epoch time.

    Returns:
        One numbered line per task in execution order, "Name: HH:MM:SS" form;
        tasks that are ready to run are shown as ready.
    """
    infos = get_all_tasks_info(state, now)
    if not infos:
        return "No programmed tasks"
    lines = []
    for position, (name, remaining) in enumerate(infos, start=1):
        if remaining <= 0:
            lines.append(f"{position}. {name}: ready")
        else:
            lines.append(f"{position}. {name}: {format_time_remaining(remaining)}")
    return "\n".join(lines)


def format_instance_status(state, now, active, aborted, queue_position, retry_seconds):
    """Build the one-line status shown next to an instance.

    Args:
        state: The instance state (or None).
        now (float): Current epoch time.
        active (bool): Whether the instance has a running worker.
        aborted (bool): Whether the instance was permanently aborted.
        queue_position (int, optional): 1-based start order when waiting.
        retry_seconds (float): Remaining retry cooldown, if any.

    Returns:
        str: The status text shown in the instance row.
    """
    task_name, next_time = get_next_task_info(state, now)

    if active:
        if state is not None and state.opening:
            return f"Opening... First task: {task_name}" if task_name else "Opening..."
        if state is not None and state.current_task_name:
            return f"Executing: {state.current_task_name}"
        if task_name and next_time and next_time > now:
            return f"Next: {task_name} in {format_time_remaining(int(next_time - now))}"
        if task_name:
            return f"Executing: {task_name}"
        return "No programmed tasks"

    if aborted:
        return "Game not installed - install it and press Start"
    if queue_position:
        if task_name:
            if next_time:
                return f"In queue ({queue_position}) - Next: {task_name} in {format_time_remaining(int(next_time - now))}"
            return f"In queue ({queue_position}) - Waiting to execute: {task_name}"
        return f"In queue ({queue_position})"
    if retry_seconds > 0:
        return f"Retry paused - {format_time_remaining(int(retry_seconds))}"
    if task_name:
        if next_time:
            return f"Standby - Next: {task_name} in {format_time_remaining(int(next_time - now))}"
        return f"Standby - Waiting to execute: {task_name}"
    return "Standby"
