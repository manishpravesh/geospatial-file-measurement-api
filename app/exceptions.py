"""The upload was rejected, or a geospatial file could not be parsed."""


class UploadRejected(Exception):
    """The upload cannot be accepted. Nothing is stored."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class GeospatialParseError(Exception):
    """The file type was accepted, but the contents are not readable geospatial data."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
