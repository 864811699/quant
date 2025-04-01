import datetime


def getLocalTimeFromMilliseconds(timestamp_ms ) :
    timestamp_sec = timestamp_ms / 1000
    dt_local = datetime.datetime.fromtimestamp(timestamp_sec)
    return dt_local
