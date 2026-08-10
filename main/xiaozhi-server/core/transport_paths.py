from urllib.parse import urlsplit


MUSEUM_WEBSOCKET_PATH = "/museum/v1/"


def is_museum_websocket_path(request_target: str) -> bool:
    return urlsplit(request_target).path == MUSEUM_WEBSOCKET_PATH


async def process_museum_websocket_request(connection, request):
    if not is_museum_websocket_path(request.path):
        return connection.respond(404, "Not Found\n")
    if request.headers.get("connection", "").lower() == "upgrade":
        return None
    return connection.respond(200, "Server is running\n")
