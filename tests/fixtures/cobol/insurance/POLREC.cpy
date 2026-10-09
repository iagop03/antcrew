      ******************************************
      * POLREC  - REGISTRO DE POLIZA DE SEGURO *
      * Tabla: POLIZAS                         *
      * Copia utilizada por: POLCAR, MOVPOL,   *
      *   REHPOL, CALPRM                       *
      ******************************************
       01 POLREC.
           05 POL-NUMERO          PIC 9(10).
           05 POL-CONTRATO        PIC X(6).
           05 POL-ASEGURADO       PIC X(30).
           05 POL-FECHA-EMISION   PIC 9(8).
           05 POL-FECHA-EFECT     PIC 9(8).
           05 POL-FECHA-VCTO      PIC 9(8).
           05 POL-FECHA-REHAB     PIC 9(8)  VALUE ZEROS.
           05 POL-MONTO-ASEG      PIC 9(12)V99.
           05 POL-PRIMA-MENSUAL   PIC 9(8)V99.
           05 POL-PLAZO-MESES     PIC 9(4).
           05 POL-ESTADO          PIC X(3).
               88 POL-ACTIVA               VALUE 'ACT'.
               88 POL-VENCIDA              VALUE 'VEN'.
               88 POL-REHABILITADA         VALUE 'RHB'.
               88 POL-CANCELADA            VALUE 'CAN'.
           05 POL-MESES-VENCIDA   PIC 9(4)  VALUE ZEROS.
           05 POL-AGENTE          PIC X(6).
           05 POL-SUCURSAL        PIC X(4).
