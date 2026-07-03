/*
 *FIRMWARE v15.0
 * Arduino UNO / ATmega328P
 * 
 * VERSIÓN SIMPLIFICADA - SIN BUFFER DE COMANDOS
 * Solo comandos de texto: ZERO, HOME, MOV, STEP, STATUS, STOP, PAUSE, RESUME, TESTZ
 *                        LIST, SELEC, BORDAR, START, END
 * Bordado directo desde SD, sin buffer intermedio
 * 
 * MODIFICACIÓN: Cooler en Pin 9 (controlado por puertos)
 */

#include <SPI.h>
#include <SD.h>
#include <EEPROM.h>

#define SD_CS_PIN 10
#define EEPROM_ADDR_X 0
#define EEPROM_ADDR_Y 4
#define EEPROM_ADDR_ST 8

// ========== PINES ==========
#define STEP_X_PIN      2
#define STEP_Y_PIN      3
#define STEP_Z_PIN      4
#define DIR_X_PIN       5
#define DIR_Y_PIN       6
#define DIR_Z_PIN       7
#define ENABLE_XY_PIN   8
#define ENABLE_Z_PIN    A3
#define TENSION_PIN     A0
#define STATUS_LED_PIN  A1
#define SENSOR_Z_PIN    A2
#define LIMIT_X_PIN     A4
#define LIMIT_Y_PIN     A5

// ========== COOLER (Pin 9) ==========
#define COOLER_PIN    PORTB1        // Pin 9
#define COOLER_PORT   PORTB
#define COOLER_DDR    DDRB

#define COOLER_ON()   (COOLER_PORT |=  (1 << COOLER_PIN)) //  "Usamos operaciones lógicas a nivel de bit para modificar selectivamente un pin 
#define COOLER_OFF()  (COOLER_PORT &= ~(1 << COOLER_PIN)) // dentro de un puerto, sin alterar el estado del resto."

// ========== MACROS ==========
#define STEP_X_HIGH()    (PORTD |=  (1<<2))
#define STEP_X_LOW()     (PORTD &= ~(1<<2))
#define STEP_Y_HIGH()    (PORTD |=  (1<<3))
#define STEP_Y_LOW()     (PORTD &= ~(1<<3))
#define STEP_Z_HIGH()    (PORTD |=  (1<<4))
#define STEP_Z_LOW()     (PORTD &= ~(1<<4))
#define DIR_X_SET(v)     do{ if(v) PORTD|=(1<<5); else PORTD&=~(1<<5); }while(0)
#define DIR_Y_SET(v)     do{ if(v) PORTD&=~(1<<6); else PORTD|=(1<<6); }while(0)
#define DIR_Z_SET(v)     do{ if(v) PORTD|=(1<<7); else PORTD&=~(1<<7); }while(0)

#define ENABLE_XY_ON()   (PORTB &= ~(1<<0))
#define ENABLE_XY_OFF()  (PORTB |=  (1<<0))
#define ENABLE_Z_ON()    (PORTC |=  (1<<3))
#define ENABLE_Z_OFF()   (PORTC &= ~(1<<3))

#define TENSION_ON()     (PORTC &= ~(1<<0))
#define TENSION_OFF()    (PORTC |=  (1<<0))
#define LED_ON()         (PORTC |=  (1<<1))
#define LED_OFF()        (PORTC &= ~(1<<1))
#define SENSOR_Z_HOLE()  (!(PINC & (1<<2)))
#define LIMIT_X()        (!(PINC & (1<<4)))
#define LIMIT_Y()        (!(PINC & (1<<5)))

// ========== PROTOCOLO ==========
#define FLAG_STITCH       0x01
#define FLAG_TRIM         0x02
#define FLAG_COLOR_CHANGE 0x04
#define FLAG_END_DESIGN   0x10

// ========== CONFIGURACION ==========
#define VEL_STITCH        20000      
#define VEL_JUMP          25000      
#define VEL_MIN           80
#define VEL_MAX           30000
#define RAMP_MAX_STEPS    800
#define RAMP_MIN_STEPS    100
#define SHORT_MOVE_LIMIT  50
#define SALTO_MIN_PASOS   3500
#define Z_INTERVAL_US     300
#define Z_MAX_PASOS       1700
#define Z_TIMEOUT_MS      3000
#define Z_ESCAPE_PASOS    40
#define STEPS_PER_MM      390

// ========== ESTADOS ==========
typedef enum { 
    SYS_IDLE=0, 
    SYS_MOVING=1, 
    SYS_Z_BUSY=2, 
    SYS_ESTOP=3, 
    SYS_ERROR=4, 
    SYS_PAUSED=5,
    SYS_BORDANDO=6
} SysState;
static volatile SysState sys_state = SYS_IDLE;

// ========== VARIABLES GLOBALES ==========
static volatile int32_t pos_x = 0, pos_y = 0;
static volatile int32_t bres_dx=0, bres_dy=0, bres_err=0;
static volatile int8_t  bres_sx=0, bres_sy=0;
static volatile int32_t bres_total=0, bres_cnt=0;
static volatile bool    bres_major_x=false;
static volatile uint16_t target_period=1000, current_period=2000;
static volatile int32_t ramp_steps=0;
static volatile bool    estop_flag=false;
static volatile uint32_t stitch_count=0;
static volatile bool    tension_on=false, salto_largo=false;
static volatile uint32_t timer_ms=0;
static volatile int32_t home_x = 0, home_y = 0;

// Estados para transferencia
bool guardando = false;
File archivo_rx;
File archivo_bordado;
char archivo_nombre[32] = "";
char archivo_seleccionado[32] = "";
bool bordando = false;
bool pausa = false;
uint32_t comandos_ejecutados = 0;
uint32_t total_comandos = 0;
bool encontrado_start = false;

// ========== FUNCIONES ==========
static inline int32_t atomic_read32(volatile int32_t* v) { int32_t r; cli(); r=*v; sei(); return r; }
static inline uint32_t atomic_read32u(volatile uint32_t* v) { uint32_t r; cli(); r=*v; sei(); return r; }

// ========== COOLER ==========
static inline void cooler_encender() {
    COOLER_ON();
    Serial.println(F("COOLER: ON"));
}

static inline void cooler_apagar() {
    COOLER_OFF();
    Serial.println(F("COOLER: OFF"));
}

void eeprom_save_position() {
    EEPROM.put(EEPROM_ADDR_X, pos_x);
    EEPROM.put(EEPROM_ADDR_Y, pos_y);
    EEPROM.put(EEPROM_ADDR_ST, stitch_count);
}

void eeprom_load_position() {
    EEPROM.get(EEPROM_ADDR_X, pos_x);
    EEPROM.get(EEPROM_ADDR_Y, pos_y);
    EEPROM.get(EEPROM_ADDR_ST, stitch_count);
}

bool leerLinea(char* buffer, int max_len) {
    static int idx = 0;
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\r') continue;
        if (c == '\n') {
            buffer[idx] = '\0';
            idx = 0;
            return true;
        }
        if (idx < max_len - 1) buffer[idx++] = c;
    }
    return false;
}

ISR(TIMER0_COMPA_vect) { timer_ms++; }

ISR(TIMER1_COMPA_vect) {
    if (estop_flag || (sys_state != SYS_MOVING && sys_state != SYS_BORDANDO)) {
        if (sys_state != SYS_IDLE && sys_state != SYS_PAUSED) {
            sys_state = SYS_IDLE;
            STEP_X_LOW(); STEP_Y_LOW();
            if (salto_largo) { salto_largo=false; tension_on=false; }
        }
        return;
    }

    if (ramp_steps == 0) {
        current_period = target_period;
    } else {
        int32_t cnt=bres_cnt, total=bres_total, ramp=ramp_steps;
        if (cnt < ramp) {
            uint32_t sp = (uint32_t)target_period * 3UL;
            if (sp > 6000UL) sp = 6000UL;
            current_period = (uint16_t)(sp - (uint32_t)(sp - target_period) * (uint32_t)cnt / (uint32_t)ramp);
        } else if (total > ramp * 2L && cnt > (total - ramp)) {
            int32_t ds = total - cnt; if (ds < 0) ds = 0;
            uint32_t sp = (uint32_t)target_period * 3UL;
            if (sp > 6000UL) sp = 6000UL;
            current_period = (uint16_t)(sp - (uint32_t)(sp - target_period) * (uint32_t)ds / (uint32_t)ramp);
        } else {
            current_period = target_period;
        }
    }
    if (current_period < 200) current_period = 200;
    OCR1A = current_period;

    if (bres_cnt >= bres_total) {
        sys_state = SYS_IDLE;
        if (salto_largo) { salto_largo=false; tension_on=false; TENSION_OFF(); }
        STEP_X_LOW(); STEP_Y_LOW(); 
        return;
    }

    bool sx=false, sy=false;
    if (bres_major_x) {
        sx=true; bres_err += bres_dy;
        if (bres_err >= 0) { bres_err -= bres_dx; sy=true; }
    } else {
        sy=true; bres_err += bres_dx;
        if (bres_err >= 0) { bres_err -= bres_dy; sx=true; }
    }
    bres_cnt++;
    if (sx) { pos_x += bres_sx; }
    if (sy) { pos_y += bres_sy; }
    if (sx) { STEP_X_HIGH(); __asm__ __volatile__("nop\nnop\nnop\nnop\nnop\nnop"); STEP_X_LOW(); }
    if (sy) { STEP_Y_HIGH(); __asm__ __volatile__("nop\nnop\nnop\nnop\nnop\nnop"); STEP_Y_LOW(); }
}

static bool z_vuelta() {
    cli(); sys_state = SYS_Z_BUSY; sei();
    
    ENABLE_Z_ON(); DIR_Z_SET(1); _delay_us(5);

    if (SENSOR_Z_HOLE()) {
        for (uint8_t i=0; i<Z_ESCAPE_PASOS; i++) {
            STEP_Z_HIGH(); _delay_us(2); STEP_Z_LOW();
            _delay_us(Z_INTERVAL_US);
        }
    }
    
    uint32_t start_time = timer_ms;
    uint16_t p = 0;
    bool ok = false;
    
    while (p < Z_MAX_PASOS) {
        if ((timer_ms - start_time) > Z_TIMEOUT_MS) break;
        
        STEP_Z_HIGH(); _delay_us(2); STEP_Z_LOW();
        _delay_us(Z_INTERVAL_US); p++;
        
        if (SENSOR_Z_HOLE()) { ok = true; break; }
    }
    
    STEP_Z_LOW(); ENABLE_Z_OFF();
    
    if (!ok) { 
        cli(); sys_state = SYS_ERROR; estop_flag = true; sei(); 
        ENABLE_XY_OFF(); LED_OFF(); 
        return false; 
    }
    
    cli(); sys_state = SYS_IDLE; sei();
    return true;
}

static void mover_relativo(int32_t dx, int32_t dy, bool es_stitch) {
    if (estop_flag) return;
    
    if (dx == 0 && dy == 0) {
        if (es_stitch) {
            stitch_count++;
            eeprom_save_position();
        }
        return;
    }
    
    bres_dx = (dx>=0) ? dx : -dx;
    bres_dy = (dy>=0) ? dy : -dy;
    bres_sx = (dx>=0) ? 1 : -1;
    bres_sy = (dy>=0) ? 1 : -1;
    
    if (bres_dx >= bres_dy) {
        bres_major_x = true;
        bres_total = bres_dx;
        bres_err = -(bres_dx/2);
    } else {
        bres_major_x = false;
        bres_total = bres_dy;
        bres_err = -(bres_dy/2);
    }
    bres_cnt = 0;
    
    DIR_X_SET(dx>=0);
    DIR_Y_SET(dy>=0);
    
    uint16_t vel = es_stitch ? VEL_STITCH : VEL_JUMP;
    if (vel < VEL_MIN) vel = VEL_MIN;
    if (vel > VEL_MAX) vel = VEL_MAX;
    target_period = (uint16_t)(2000000UL / vel);
    if (target_period < 200) target_period = 200;
    
    if (bres_total <= SHORT_MOVE_LIMIT) {
        ramp_steps = 0;
        current_period = target_period;
    } else {
        ramp_steps = bres_total / 3;
        if (ramp_steps < RAMP_MIN_STEPS) ramp_steps = RAMP_MIN_STEPS;
        if (ramp_steps > RAMP_MAX_STEPS) ramp_steps = RAMP_MAX_STEPS;
        uint32_t sp = (uint32_t)target_period * 3UL;
        current_period = (sp > 6000UL) ? 6000U : (uint16_t)sp;
    }
    
    ENABLE_XY_ON();
    cli(); 
    if (sys_state == SYS_PAUSED) {
        sei();
        return;
    }
    sys_state = SYS_MOVING; 
    TCNT1 = 0; 
    OCR1A = current_period; 
    sei();
}

// ========== MOSTRAR PUNTADA ==========
void mostrar_puntada(int num, int32_t dx, int32_t dy, int flags) {
    float dx_mm = dx / STEPS_PER_MM;
    float dy_mm = dy / STEPS_PER_MM;
    
    Serial.print(num);
    Serial.print(F("\t"));
    Serial.print(dx_mm, 2);
    Serial.print(F("\t"));
    Serial.print(dy_mm, 2);
    Serial.print(F("\t"));
    
    if (flags & FLAG_STITCH) {
        Serial.println(F("STITCH"));
    } else if (flags == 0) {
        Serial.println(F("JUMP"));
    } else if (flags & FLAG_COLOR_CHANGE) {
        Serial.println(F("COLOR"));
    } else {
        Serial.println(flags);
    }
}

// ========== COMANDOS ==========

void set_zero() {
    if (bordando || pausa) {
        Serial.println(F("ERROR: No se puede resetear posicion durante bordado"));
        return;
    }
    
    cli();
    pos_x = 0;
    pos_y = 0;
    stitch_count = 0;
    home_x = 0;
    home_y = 0;
    sei();
    
    eeprom_save_position();
    
    Serial.println(F("ZERO OK"));
    mostrar_status();
}

void ir_a_home() {
    if (bordando || pausa) {
        Serial.println(F("ERROR: No se puede hacer homing durante bordado"));
        return;
    }
    
    ENABLE_XY_ON();
    
    Serial.println(F("HOMING: Buscando limite X..."));
    DIR_X_SET(0);
    while (!LIMIT_X()) {
        STEP_X_HIGH(); _delay_us(500); 
        STEP_X_LOW(); _delay_us(500);
    }
    STEP_X_LOW();
    
    Serial.println(F("HOMING: Buscando limite Y..."));
    DIR_Y_SET(0);
    while (!LIMIT_Y()) {
        STEP_Y_HIGH(); _delay_us(500); 
        STEP_Y_LOW(); _delay_us(500);
    }
    STEP_Y_LOW();
    
    cli();
    pos_x = 0;
    pos_y = 0;
    home_x = 0;
    home_y = 0;
    stitch_count = 0;
    sei();
    
    eeprom_save_position();
    
    Serial.println(F("HOME OK"));
    mostrar_status();
}

void mover_absoluto(int32_t x, int32_t y) {
    int32_t dx = x - atomic_read32(&pos_x);
    int32_t dy = y - atomic_read32(&pos_y);
    mover_relativo(dx, dy, false);
    while ((sys_state == SYS_MOVING || sys_state == SYS_BORDANDO) && !estop_flag && !pausa) delayMicroseconds(100);
    mostrar_status();
}

void mover_pasos(int32_t pasos) {
    mover_relativo(pasos, 0, false);
    while ((sys_state == SYS_MOVING || sys_state == SYS_BORDANDO) && !estop_flag && !pausa) delayMicroseconds(100);
    mostrar_status();
}

void mostrar_status() {
    Serial.print(F("POS: X="));
    Serial.print(atomic_read32(&pos_x));
    Serial.print(F(" Y="));
    Serial.print(atomic_read32(&pos_y));
    Serial.print(F(" ST="));
    Serial.println(atomic_read32u(&stitch_count));
}

void emergencia_stop() {
    cli(); 
    estop_flag = true; 
    bordando = false;
    pausa = false;
    sys_state = SYS_ESTOP; 
    if (archivo_bordado) archivo_bordado.close();
    sei();
    STEP_X_LOW(); STEP_Y_LOW(); STEP_Z_LOW();
    ENABLE_XY_OFF(); ENABLE_Z_OFF();
    TENSION_OFF(); LED_OFF();
    cooler_apagar();  // ← APAGAR COOLER EN EMERGENCIA
    Serial.println(F("STOP"));
}

void pausar_bordado() {
    if (bordando && !pausa) {
        pausa = true;
        sys_state = SYS_PAUSED;
        Serial.println(F("PAUSADO"));
    }
}

void reanudar_bordado() {
    if (bordando && pausa) {
        pausa = false;
        sys_state = SYS_BORDANDO;
        Serial.println(F("REANUDADO"));
    }
}

void listar_archivos_sd() {
    Serial.println(F("Archivos SD:"));
    File root = SD.open("/");
    if (!root) { 
        Serial.println(F("ERROR")); 
        return; 
    }
    
    while (true) {
        File entry = root.openNextFile();
        if (!entry) break;
        if (!entry.isDirectory()) {
            Serial.print(F("  "));
            Serial.println(entry.name());
        }
        entry.close();
    }
    root.close();
    Serial.println(F("FIN_LISTA"));
}

void procesar_siguiente_comando() {
    if (!bordando || pausa || estop_flag) return;
    if (!archivo_bordado) {
        bordando = false;
        return;
    }
    
    char linea[32];
    int idx = 0;
    while (archivo_bordado.available() && idx < 31) {
        char c = archivo_bordado.read();
        if (c == '\n') break;
        if (c != '\r') linea[idx++] = c;
    }
    linea[idx] = '\0';
    
    if (idx == 0) {
        archivo_bordado.close();
        bordando = false;
        sys_state = SYS_IDLE;
        cooler_apagar();  // ← APAGAR COOLER AL TERMINAR
        Serial.println(F("BORDADO COMPLETADO"));
        mostrar_status();
        return;
    }
    
    if (!encontrado_start) {
        if (strcmp(linea, "#START") == 0) {
            encontrado_start = true;
        }
        return;
    }
    
    if (linea[0] == '#' || linea[0] == '\0') return;
    
    int dx, dy, flags;
    sscanf(linea, "%d,%d,%d", &dx, &dy, &flags);
    
    comandos_ejecutados++;
    mostrar_puntada(comandos_ejecutados, dx, dy, flags);
    
    // Manejar movimientos CERO
    if (dx == 0 && dy == 0) {
        if (flags & FLAG_COLOR_CHANGE) {
            Serial.println(F("COLOR_CHANGE: Esperando cambio de hilo..."));
            pausa = true;
            sys_state = SYS_PAUSED;
            return;
        }
        if (flags & FLAG_STITCH) {
            stitch_count++;
            eeprom_save_position();
        }
        return;
    }
    
    bool es_stitch = (flags & FLAG_STITCH) != 0;
    mover_relativo(dx, dy, es_stitch);
    
    uint32_t t0 = timer_ms;
    while ((sys_state == SYS_MOVING || sys_state == SYS_BORDANDO) && !estop_flag && !pausa) {
        delayMicroseconds(100);
        if ((timer_ms - t0) > 8000UL) {
            Serial.println(F("ERROR: Movimiento timeout"));
            emergencia_stop();
            return;
        }
    }
    
    if (estop_flag || pausa) return;
    
    if (es_stitch) {
        stitch_count++;
        if (!z_vuelta()) {
            Serial.println(F("ERROR Z"));
            emergencia_stop();
            return;
        }
        eeprom_save_position();
    }
    
    static uint32_t last_progress = 0;
    if (millis() - last_progress > 1000) {
        last_progress = millis();
        mostrar_status();
    }
}

// ========== SETUP ==========
void setup() {
    Serial.begin(115200);
    Serial.println(F("BORDADOR CNC v15.7"));
    Serial.println(F("Comandos: ZERO, HOME, MOV X Y, STEP S, STATUS, STOP, PAUSE, RESUME, TESTZ"));
    Serial.println(F("          LIST, SELEC nombre, BORDAR, START nombre, END"));
    
    // Configurar pines
    DDRD |= (1<<2)|(1<<3)|(1<<4)|(1<<5)|(1<<6)|(1<<7);
    DDRB |= (1<<0);
    DDRC |= (1<<0)|(1<<1)|(1<<3)|(1<<4)|(1<<5);
    
    // ★ COOLER: Configurar pin 9 como salida
    COOLER_DDR |= (1 << COOLER_PIN);
    COOLER_OFF();  // Apagado al inicio
    
    ENABLE_XY_OFF(); ENABLE_Z_OFF(); TENSION_OFF(); LED_ON();
    STEP_X_LOW(); STEP_Y_LOW(); STEP_Z_LOW(); DIR_Z_SET(1);
    
    TCCR0A = (1 << WGM01); TCCR0B = (1 << CS01) | (1 << CS00);
    OCR0A = 249; TIMSK0 |= (1 << OCIE0A);
    
    TCCR1A = 0; TCCR1B = (1 << WGM12) | (1 << CS11);
    OCR1A = 2000; TIMSK1 |= (1 << OCIE1A);
    
    eeprom_load_position();
    
    if (!SD.begin(SD_CS_PIN)) {
        Serial.println(F("SD ERROR"));
    } else {
        Serial.println(F("SD OK"));
    }
    
    sei();
    mostrar_status();
}

// ========== LOOP PRINCIPAL ==========
void loop() {
    if (tension_on) TENSION_ON(); else TENSION_OFF();
    
    if (bordando && !pausa && !estop_flag && sys_state == SYS_IDLE) {
        procesar_siguiente_comando();
    }
    
    if (Serial.available()) {
        char cmd[40];
        if (leerLinea(cmd, 40)) {
            
            if (strcmp(cmd, "ZERO") == 0) {
                set_zero();
            }
            else if (strcmp(cmd, "HOME") == 0) {
                ir_a_home();
            }
            else if (strncmp(cmd, "MOV ", 4) == 0) {
                int x, y;
                if (sscanf(cmd + 4, "%d %d", &x, &y) == 2) {
                    mover_absoluto(x, y);
                }
            }
            else if (strncmp(cmd, "STEP ", 5) == 0) {
                int pasos;
                if (sscanf(cmd + 5, "%d", &pasos) == 1) {
                    mover_pasos(pasos);
                }
            }
            else if (strcmp(cmd, "STATUS") == 0) {
                mostrar_status();
            }
            else if (strcmp(cmd, "STOP") == 0) {
                emergencia_stop();
            }
            else if (strcmp(cmd, "PAUSE") == 0) {
                pausar_bordado();
            }
            else if (strcmp(cmd, "RESUME") == 0) {
                reanudar_bordado();
            }
            else if (strcmp(cmd, "TESTZ") == 0) {
                Serial.println(F("TESTZ: Iniciando..."));
                unsigned long start = millis();
                if (z_vuelta()) {
                    Serial.print(F("TESTZ: OK - Tiempo: "));
                    Serial.print(millis() - start);
                    Serial.println(F(" ms"));
                } else {
                    Serial.println(F("TESTZ: ERROR"));
                }
                mostrar_status();
            }
            else if (strcmp(cmd, "LIST") == 0) {
                listar_archivos_sd();
            }
            else if (strncmp(cmd, "SELEC ", 6) == 0) {
                strncpy(archivo_seleccionado, cmd + 6, 31);
                archivo_seleccionado[31] = '\0';
                Serial.print(F("Seleccionado: "));
                Serial.println(archivo_seleccionado);
            }
            else if (strcmp(cmd, "BORDAR") == 0) {
                if (strlen(archivo_seleccionado) == 0) {
                    Serial.println(F("ERROR: No hay archivo seleccionado"));
                } else {
                    File temp = SD.open(archivo_seleccionado);
                    total_comandos = 0;
                    while (temp.available()) {
                        if (temp.read() == '\n') total_comandos++;
                    }
                    temp.close();
                    
                    archivo_bordado = SD.open(archivo_seleccionado);
                    if (!archivo_bordado) {
                        Serial.println(F("ERROR: No se pudo abrir"));
                    } else {
                        comandos_ejecutados = 0;
                        encontrado_start = false;
                        bordando = true;
                        pausa = false;
                        estop_flag = false;
                        sys_state = SYS_BORDANDO;
                        
                        // ★ ENCENDER COOLER AL EMPEZAR BORDADO
                        cooler_encender();
                        
                        Serial.println(F("BORDANDO..."));
                        Serial.println(F("Num\tdx_mm\tdy_mm\ttipo"));
                    }
                }
            }
            else if (strncmp(cmd, "START ", 6) == 0) {
                if (guardando) {
                    Serial.println(F("ERROR: Ya recibiendo"));
                } else {
                    strncpy(archivo_nombre, cmd + 6, 31);
                    archivo_nombre[31] = '\0';
                    archivo_rx = SD.open(archivo_nombre, FILE_WRITE);
                    if (!archivo_rx) {
                        Serial.println(F("ERROR: No se pudo crear"));
                    } else {
                        guardando = true;
                        Serial.println(F("OK"));
                    }
                }
            }
            else if (strcmp(cmd, "END") == 0) {
                if (guardando) {
                    archivo_rx.close();
                    guardando = false;
                    Serial.print(F("Recibido: "));
                    Serial.println(archivo_nombre);
                    listar_archivos_sd();
                }
            }
            else if (guardando && strlen(cmd) > 0) {
                archivo_rx.println(cmd);
                archivo_rx.flush();
                Serial.println(F("OK"));
            }
        }
    }
}
