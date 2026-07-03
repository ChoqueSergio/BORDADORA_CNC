# Bordadora CNC - Firmware y Software de Control

Proyecto completo de una bordadora CNC desarrollado desde cero utilizando Arduino y Python para el control de la máquina, la conversión de diseños y la gestión del proceso de bordado.

## Firmware para Arduino UNO / ATmega328P

El firmware fue desarrollado en C/C++ y optimizado para ejecutarse en un Arduino UNO utilizando acceso directo a registros y puertos del microcontrolador para obtener la máxima velocidad y precisión en el control de los motores.

### Funcionalidades del firmware

* Control independiente de los ejes X, Y y Z mediante motores paso a paso.
* Generación de movimientos interpolados utilizando el algoritmo de Bresenham.
* Control de aceleración y desaceleración mediante rampas automáticas.
* Ejecución directa de diseños almacenados en tarjeta MicroSD.
* Gestión de cambios de color durante el bordado.
* Control automático del mecanismo de aguja mediante el eje Z.
* Sistema de homing mediante finales de carrera.
* Parada de emergencia por software.
* Funciones de pausa y reanudación del trabajo.
* Almacenamiento de posición y cantidad de puntadas en EEPROM.
* Control del sistema de tensión del hilo.
* Activación automática del sistema de refrigeración durante el bordado.
* Comunicación serial mediante comandos de texto simples y fáciles de integrar.

### Comandos soportados

* `ZERO`
* `HOME`
* `MOV X Y`
* `STEP`
* `STATUS`
* `STOP`
* `PAUSE`
* `RESUME`
* `TESTZ`
* `LIST`
* `SELEC`
* `BORDAR`

## Software de control en Python

La aplicación de escritorio fue desarrollada completamente en Python utilizando Tkinter para proporcionar una interfaz gráfica intuitiva y ligera para el control de la máquina.

### Funcionalidades del software

* Conexión automática mediante puerto serial.
* Control manual de movimiento en milímetros.
* Monitor serial en tiempo real.
* Gestión de archivos almacenados en la tarjeta SD.
* Selección e inicio de trabajos de bordado.
* Configuración de pasos por milímetro.
* Conversión automática de archivos DST a comandos compatibles con el firmware.
* Visualización previa del diseño antes de iniciar el bordado.
* Zoom, desplazamiento y navegación dentro del patrón.
* Identificación visual de cambios de color.
* Exportación del diseño convertido listo para ejecutar en la máquina.

## Librerías utilizadas

### Firmware

* SPI
* SD
* EEPROM

### Software

* Tkinter
* PySerial
* PyEmbroidery
* Threading
* Queue
* JSON

## Características técnicas destacadas

* Comunicación serial a 115200 baudios.
* Procesamiento de archivos DST compatibles con el formato Tajima.
* Conversión configurable a milímetros o pasos del motor.
* Arquitectura sin buffer intermedio para reducir el consumo de memoria.
* Uso de interrupciones para el control preciso del movimiento.
* Manipulación directa de registros del ATmega328P para maximizar el rendimiento del sistema.
