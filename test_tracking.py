from src.tracking_service import TCITrackingService
import json


service = TCITrackingService()

result = service.track_dwb("299106861")

print(
    json.dumps(
        result,
        indent=4,
        ensure_ascii=False
    )
)