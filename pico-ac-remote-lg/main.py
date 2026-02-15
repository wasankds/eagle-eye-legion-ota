import machine
from machine import Pin, PWM
import utime
import dht
import config
import wifi_manager
import gc
from umqtt.simple import MQTTClient

# --- 0. System Config & Debug ---
DEBUG = True  # เปิด True ไว้ก่อนเพื่อดู Log ตอนทดสอบ ถ้าทุกอย่างนิ่งแล้วค่อยแก้เป็น False ครับ
SYSNAME = "Eagle Eye Legion"
SYSVER  = "1.0.4"  # อัปเกรดเวอร์ชันเพื่อให้ Senko ตรวจพบการเปลี่ยนแปลง

def log(msg):
    if DEBUG:
        print(msg)

# --- 1. Hardware Setup ---
LED = Pin("LED", Pin.OUT)
IR_LED = PWM(Pin(15))
IR_LED.freq(38000) 
IR_LED.duty_u16(0)

BTN_ON    = Pin(14, Pin.IN)
BTN_OFF   = Pin(13, Pin.IN)
BTN_LIGHT = Pin(12, Pin.IN)
SENSOR    = dht.DHT11(Pin(16))

# --- 2. System Variables ---
CLIENT = None
MQTT_CONNECTED = False
LAST_MQTT_RECONNECT = 0
LAST_DHT_SEND = 0
LAST_PING = 0  # ตัวแปรใหม่สำหรับคุมจังหวะการส่งสัญญาณชีพ

# --- 3. Functions ---

def send_ir(raw_data, name):
    LED.on()
    log(f"[IR] Sending {name}...")
    IR_LED.duty_u16(21845); utime.sleep_us(9000); IR_LED.duty_u16(0); utime.sleep_us(4500)
    for i in range(0, len(raw_data)-1, 2):
        IR_LED.duty_u16(21845); utime.sleep_us(560); IR_LED.duty_u16(0)
        utime.sleep_us(1690 if raw_data[i+1] > 1000 else 560)
    IR_LED.duty_u16(21845); utime.sleep_us(560); IR_LED.duty_u16(0)
    LED.off()

def trigger_ota():
    global CLIENT, MQTT_CONNECTED
    log("\n[OTA] Preparing for Update...")
    try:
        if CLIENT:
            CLIENT.publish(config.TOPIC_AVAIL, "UPDATING", retain=True)
            utime.sleep_ms(500)
            CLIENT.disconnect()
    except: pass
    
    CLIENT = None
    MQTT_CONNECTED = False
    gc.collect() 
    utime.sleep(1)

    try:
        import senko
        gc.collect()
        OTA = senko.Senko(user=config.OTA_USER, repo=config.OTA_REPO, working_dir=config.OTA_DIR, files=config.OTA_FILES)
        if OTA.update():
            log("[OTA] Update Success! Rebooting...")
            utime.sleep(2)
            machine.reset()
        else:
            log("[OTA] No New Version.")
            utime.sleep(1)
            machine.reset()
    except Exception as e:
        log(f"[OTA] Failed: {e}")
        utime.sleep(2)
        machine.reset()

def send_dht_data():
    for i in range(3): 
        try:
            utime.sleep_ms(200)
            SENSOR.measure()
            t, h = SENSOR.temperature(), SENSOR.humidity()
            msg = '{{"t": "{}", "h": "{}"}}'.format(t, h)
            if CLIENT:
                CLIENT.publish(config.TOPIC_SENSOR_DHT, msg)
            log(f"[DATA] Sent DHT: {t}C / {h}% (Try {i+1})")
            gc.collect()
            return 
        except Exception as e:
            log(f"[DATA] DHT Try {i+1} Failed: {e}")
            utime.sleep_ms(500)
    log("[DATA] DHT Final Error: Give up")

def on_message(topic, msg):
    t = topic.decode()
    m = msg.decode().upper()
    log(f"\n[MQTT] Incoming: {t} -> {m}")
    
    if t.endswith(config.TOPIC_UPDATE) and m == "UPDATE":
        trigger_ota()
        return

    if t == config.TOPIC_AC_ON: send_ir(config.RAW_ON, "POWER ON")
    elif t == config.TOPIC_AC_OFF: send_ir(config.RAW_OFF, "POWER OFF")
    elif t == config.TOPIC_AC_LED: send_ir(config.RAW_LIGHT, "LIGHT")
    elif t == config.TOPIC_QUERY: 
      log("[QUERY] Status Requested")
      try:
        CLIENT.publish(config.TOPIC_AVAIL, "ONLINE", retain=True, qos=1)
      except: pass
      send_dht_data()

def try_mqtt_connect():
    global CLIENT, MQTT_CONNECTED
    log("Connecting to MQTT...")
    try:
        # --- ล้างข้อมูลเก่าก่อนเริ่มใหม่ (ป้องกันแรมเต็ม) ---
        if CLIENT is not None:
            try:
                CLIENT.disconnect()
            except: pass
        gc.collect()
        # -------------------------------------------
        
        CLIENT = MQTTClient(config.CLIENT_ID, config.MQTT_BROKER, keepalive=60)
        CLIENT.set_callback(on_message)
        CLIENT.set_last_will(config.TOPIC_AVAIL, "OFFLINE", retain=True, qos=1)
        CLIENT.connect()
        
        topics = [config.TOPIC_AC_ON, config.TOPIC_AC_OFF, config.TOPIC_AC_LED, config.TOPIC_QUERY, config.TOPIC_UPDATE]
        for top in topics:
            CLIENT.subscribe(top)
        
        CLIENT.publish(config.TOPIC_AVAIL, "ONLINE", retain=True, qos=1)
        MQTT_CONNECTED = True
        log("MQTT Status: Connected and ONLINE")
        return True
    except Exception as e:
        log(f"MQTT Failed: {e}")
        MQTT_CONNECTED = False
        return False

def is_pressed(pin):
    if pin.value() == 0:
        utime.sleep_ms(80) 
        return pin.value() == 0
    return False

# --- Start Up Sequence ---
log(f"\n{SYSNAME} v{SYSVER} (Starting...)")
for _ in range(3): LED.on(); utime.sleep(0.05); LED.off(); utime.sleep(0.05)

log("Connecting to WiFi...")
LED.on() 
if wifi_manager.connect_wifi(config.WIFI_CONFIGS):
    LED.off(); utime.sleep(0.2)
    for _ in range(2): LED.on(); utime.sleep(0.1); LED.off(); utime.sleep(0.1)
    
    log(f"Connecting to MQTT Broker {config.MQTT_BROKER}")
    LED.on() 
    if try_mqtt_connect():
        LED.off(); utime.sleep(0.2)
        LED.on(); utime.sleep(1.5); LED.off()
        log("SYSTEM READY!")
    else:
        for _ in range(10): LED.on(); utime.sleep(0.05); LED.off(); utime.sleep(0.05)
else:
    LED.off()
    for _ in range(3): LED.on(); utime.sleep(0.5); LED.off(); utime.sleep(0.5)

# --- Main Loop ---
while True:
    now = utime.ticks_ms()
    
    if MQTT_CONNECTED:
        try:
            CLIENT.check_msg()
            
            # --- ส่ง Ping ทุก 30 วินาที เพื่อรักษาสัญญาณกับ Broker ---
            if utime.ticks_diff(now, LAST_PING) > 30000:
                CLIENT.ping()
                LAST_PING = now
            # ------------------------------------------------
                
        except Exception as e:
            log(f"MQTT Lost Connection: {e}")
            MQTT_CONNECTED = False
            
    elif utime.ticks_diff(now, LAST_MQTT_RECONNECT) > 15000:
        log("Attempting to Reconnect MQTT...")
        try_mqtt_connect()
        LAST_MQTT_RECONNECT = now

    # ส่งข้อมูล DHT ทุก 10 วินาที
    if MQTT_CONNECTED and utime.ticks_diff(now, LAST_DHT_SEND) > 10000:
        send_dht_data()
        LAST_DHT_SEND = now

    # เช็คการกดปุ่มทางกายภาพ
    if is_pressed(BTN_ON):
        send_ir(config.RAW_ON, "ON")
        while BTN_ON.value() == 0: utime.sleep_ms(20)

    if is_pressed(BTN_OFF):
        send_ir(config.RAW_OFF, "OFF")
        while BTN_OFF.value() == 0: utime.sleep_ms(20)
            
    if is_pressed(BTN_LIGHT):
        send_ir(config.RAW_LIGHT, "LIGHT")
        while BTN_LIGHT.value() == 0: utime.sleep_ms(20)

    utime.sleep_ms(20)

