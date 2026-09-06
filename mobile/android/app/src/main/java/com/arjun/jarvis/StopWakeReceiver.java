package com.arjun.jarvis;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class StopWakeReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        context.stopService(new Intent(context, WakeService.class));
    }
}
