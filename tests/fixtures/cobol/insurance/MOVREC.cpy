      ******************************************
      * MOVREC  - REGISTRO DE MOVIMIENTO      *
      * Tabla: MOVIMIENTOS                    *
      * Copia utilizada por: MOVPOL, REHPOL   *
      ******************************************
       01 MOVREC.
           05 MOV-ID              PIC 9(12).
           05 MOV-POL-NUMERO      PIC 9(10).
           05 MOV-TIPO            PIC X(3).
               88 MOV-PAGO                 VALUE 'PAG'.
               88 MOV-CANCELACION          VALUE 'CAN'.
               88 MOV-REHABILITACION       VALUE 'RHB'.
               88 MOV-EMISION              VALUE 'EMI'.
               88 MOV-AJUSTE               VALUE 'AJU'.
           05 MOV-FECHA           PIC 9(8).
           05 MOV-FECHA-EFECT     PIC 9(8).
           05 MOV-MONTO           PIC 9(10)V99.
           05 MOV-CONCEPTO        PIC X(40).
           05 MOV-USUARIO         PIC X(8).
           05 MOV-ESTADO          PIC X(1).
               88 MOV-PENDIENTE            VALUE 'P'.
               88 MOV-APLICADO             VALUE 'A'.
               88 MOV-RECHAZADO            VALUE 'R'.
           05 MOV-MESES-REHAB     PIC 9(4)  VALUE ZEROS.
