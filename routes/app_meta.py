from flask import Blueprint, jsonify

bp = Blueprint("app_meta", __name__, url_prefix="/api/app")

# Bump these two by hand whenever a new APK is built and distributed —
# no admin UI for this yet, just redeploy after editing. These should
# match android/app/build.gradle's versionCode/versionName on whatever
# build you just shipped. The frontend compares its own running
# versionCode (read at runtime via Capacitor's App.getInfo().build)
# against latest_version_code to decide whether to show an update
# prompt, since this app is sideloaded rather than Play Store and has
# no other way to learn a newer APK exists.
LATEST_VERSION_CODE = 1
LATEST_VERSION_NAME = "1.0"
APK_URL = "https://getcamp.netlify.app"  # landing page — has the APK download link on it
# Any installed versionCode below this is blocked with a mandatory
# update screen rather than a dismissible banner. Leave at 0 (never
# force) until there's a real reason to cut off an old build.
FORCE_UPDATE_BELOW = 0


@bp.get("/version")
def get_version():
    return jsonify({
        "latest_version_code": LATEST_VERSION_CODE,
        "latest_version_name": LATEST_VERSION_NAME,
        "apk_url": APK_URL,
        "force_update_below": FORCE_UPDATE_BELOW,
    }), 200
