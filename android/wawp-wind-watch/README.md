# WAWP Wind Watch — Android prototype

Phone-only prototype for monitoring the WAWP AWOSNet feed.

## V1 behaviour
- Directly polls `http://wawp.awosnet.com/awiAwosNet.php`.
- Normal polling: **10 seconds**.
- Fast polling: **5 seconds** when the alert metric is >= 12 KT.
- Alert metric: maximum of valid `twoMinutewindSpeed` and `windGust` values received in the current AWOSNet XML. Both are displayed separately.
- **14 KT:** pre-alert notification/alarm.
- **15 KT:** stronger criterion-reached notification/alarm.
- Alert latches so repeated polls do not repeatedly alarm.
- Re-arms after the metric remains <= 12 KT for 10 minutes.
- New threshold alerts are inhibited if the AWOS observation is >3 minutes old or otherwise invalid.
- Local SQLite event history.
- Foreground monitoring service with a partial wake lock while monitoring is explicitly enabled.

## Important
This prototype is an **advisory monitor only**. It does not issue or disseminate an Aerodrome Warning. The exact operational mapping of AWOS wind/gust fields to the locally agreed `MAX 15 KT` criterion must be validated before operational use.

## Build
Open this folder in Android Studio, or run `gradle :app:assembleDebug` with Android SDK 35 installed.

Debug APK output: `app/build/outputs/apk/debug/app-debug.apk`.
