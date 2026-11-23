[app]
title = HarmoniFlow
package.name = harmoniflow
package.domain = org.harmoniflow
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,html,xml,mid
version = 0.1
requirements = python3,kivy,mido,music21,requests,urllib3,certifi,charset-normalizer,idna
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,ACCESS_NETWORK_STATE,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.api = 34
android.minapi = 24
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
