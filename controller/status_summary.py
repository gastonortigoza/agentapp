def summarize_runs(records):
    if not isinstance(records, list):
        raise TypeError
    by_status = {}
    for record in records:
        if not isinstance(record, dict):
            raise TypeError
        status = record.get("recorded_status")
        if isinstance(status, str):
            status = status.strip()
            if status == "":
                status = "unknown"
        else:
            status = "unknown"
        by_status[status] = by_status.get(status, 0) + 1
    ordered = {}
    for key in sorted(by_status.keys()):
        ordered[key] = by_status[key]
    return {"total": len(records), "by_status": ordered}
