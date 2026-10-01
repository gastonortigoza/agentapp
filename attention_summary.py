def summarize_runs(records):
    if not isinstance(records, list):
        raise TypeError
    total = 0
    by_status = {}
    needs_attention = 0
    attention_states = {"failed", "uncertain", "blocked_evidence", "blocked_budget", "uncertain_operation"}
    for record in records:
        if not isinstance(record, dict):
            raise TypeError
        total += 1
        status = record.get("recorded_status")
        if isinstance(status, str):
            status = status.strip()
            if status == "":
                status = "unknown"
        else:
            status = "unknown"
        if status in by_status:
            by_status[status] += 1
        else:
            by_status[status] = 1
        if status in attention_states:
            needs_attention += 1
    sorted_keys = sorted(by_status.keys())
    sorted_by_status = {}
    for key in sorted_keys:
        sorted_by_status[key] = by_status[key]
    return {"total": total, "by_status": sorted_by_status, "needs_attention": needs_attention}
