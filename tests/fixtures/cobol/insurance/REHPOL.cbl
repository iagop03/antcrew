      ******************************************
      * REHPOL  - REHABILITACION DE POLIZAS   *
      * COPY: POLREC, MOVREC                  *
      * CALL: VALDAT, MOVPOL                  *
      * SQL:  SELECT POLIZAS, UPDATE POLIZAS, *
      *       INSERT MOVIMIENTOS              *
      *                                       *
      * BUG (CR-2024-0891): La fecha efectiva *
      * de rehab se calcula usando            *
      * MOD(POL-MESES-VENCIDA, 12) en lugar   *
      * del valor real. Para polizas con mas  *
      * de 12 meses vencidas el resultado     *
      * es incorrecto.                        *
      ******************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. REHPOL.
       AUTHOR.     SISTEMAS JARDINERIA SA.

       DATA DIVISION.
       WORKING-STORAGE SECTION.
       COPY POLREC.
       COPY MOVREC.
       01 WS-CONTROL.
           05 WS-SQLCODE-LOCAL    PIC S9(9).
           05 WS-FECHA-HOY        PIC 9(8).
           05 WS-NUEVA-FECHA-EFECT PIC 9(8).
           05 WS-NUEVA-FECHA-VCTO  PIC 9(8).
           05 WS-MESES-VENCIDOS   PIC S9(6).
           05 WS-MESES-AJUSTE     PIC S9(4).

      * Parametros para VALDAT
       01 WS-VALDAT.
           05 WS-VD-FUNCION       PIC X(1).
           05 WS-VD-FECHA1        PIC 9(8).
           05 WS-VD-FECHA2        PIC 9(8).
           05 WS-VD-RESULTADO     PIC S9(6).
           05 WS-VD-FECHA-SAL     PIC 9(8).
           05 WS-VD-MESES         PIC S9(4).
           05 WS-VD-RETORNO       PIC 9(2).

      * Parametros para MOVPOL
       01 WS-MOVPOL.
           05 WS-MP-POL-NUMERO    PIC 9(10).
           05 WS-MP-TIPO          PIC X(3).
           05 WS-MP-MONTO         PIC 9(10)V99.
           05 WS-MP-CONCEPTO      PIC X(40).
           05 WS-MP-USUARIO       PIC X(8).
           05 WS-MP-RETORNO       PIC 9(2).

       EXEC SQL BEGIN DECLARE SECTION END-EXEC.
       01 HV-POL-NUMERO           PIC 9(10).
       01 HV-POL-ESTADO           PIC X(3).
       01 HV-POL-FECHA-VCTO       PIC 9(8).
       01 HV-POL-FECHA-EFECT      PIC 9(8).
       01 HV-POL-MESES-VENC       PIC 9(4).
       01 HV-POL-PRIMA-MENSUAL    PIC 9(10)V99.
       01 HV-POL-PLAZO-MESES      PIC 9(4).
       01 HV-NUEVA-FECHA-EFECT    PIC 9(8).
       01 HV-NUEVA-FECHA-VCTO     PIC 9(8).
       01 HV-MOV-ID               PIC 9(12).
       01 HV-MOV-MONTO            PIC 9(10)V99.
       01 HV-MOV-FECHA            PIC 9(8).
       01 HV-MOV-CONCEPTO         PIC X(40).
       01 HV-MOV-USUARIO          PIC X(8).
       EXEC SQL END DECLARE SECTION END-EXEC.

       LINKAGE SECTION.
       01 L-REHAB.
           05 L-POL-NUMERO        PIC 9(10).
           05 L-USUARIO           PIC X(8).
           05 L-PRIMA-REHAB       PIC 9(10)V99.
           05 L-CODIGO-RETORNO    PIC 9(2).

       PROCEDURE DIVISION USING L-REHAB.

           MOVE 0 TO L-CODIGO-RETORNO
           MOVE FUNCTION CURRENT-DATE(1:8) TO WS-FECHA-HOY
           MOVE L-POL-NUMERO TO HV-POL-NUMERO

      * Leer poliza y verificar estado
           EXEC SQL
               SELECT POL_ESTADO, POL_FECHA_VCTO, POL_FECHA_EFECT,
                      POL_MESES_VENCIDA, POL_PRIMA_MENSUAL, POL_PLAZO_MESES
               INTO :HV-POL-ESTADO, :HV-POL-FECHA-VCTO, :HV-POL-FECHA-EFECT,
                    :HV-POL-MESES-VENC, :HV-POL-PRIMA-MENSUAL, :HV-POL-PLAZO-MESES
               FROM POLIZAS
               WHERE POL_NUMERO = :HV-POL-NUMERO
           END-EXEC

           IF SQLCODE NOT = 0
               MOVE 30 TO L-CODIGO-RETORNO
               STOP RUN
           END-IF

           IF HV-POL-ESTADO NOT = 'VEN'
               MOVE 31 TO L-CODIGO-RETORNO
               STOP RUN
           END-IF

      *------------------------------------------------------------
      * BUG CR-2024-0891: Se usa MOD(meses-vencidos, 12) para el
      * calculo del ajuste. Para polizas con mas de 12 meses
      * vencidas esto produce una fecha efectiva incorrecta.
      * La linea correcta seria:
      *   MOVE HV-POL-MESES-VENC TO WS-VD-MESES
      *------------------------------------------------------------
           COMPUTE WS-MESES-AJUSTE = FUNCTION MOD(HV-POL-MESES-VENC, 12)
           MOVE WS-MESES-AJUSTE TO WS-VD-MESES

      * Calcular nueva fecha efectiva = hoy + meses de ajuste
           MOVE '3' TO WS-VD-FUNCION
           MOVE WS-FECHA-HOY TO WS-VD-FECHA1
           CALL 'VALDAT' USING WS-VD-FUNCION WS-VD-FECHA1
                               WS-VD-FECHA2 WS-VD-RESULTADO
                               WS-VD-FECHA-SAL WS-VD-MESES WS-VD-RETORNO
           MOVE WS-VD-FECHA-SAL TO WS-NUEVA-FECHA-EFECT

      * Calcular nueva fecha vencimiento = nueva efect + plazo original
           MOVE HV-POL-PLAZO-MESES TO WS-VD-MESES
           MOVE '3' TO WS-VD-FUNCION
           MOVE WS-NUEVA-FECHA-EFECT TO WS-VD-FECHA1
           CALL 'VALDAT' USING WS-VD-FUNCION WS-VD-FECHA1
                               WS-VD-FECHA2 WS-VD-RESULTADO
                               WS-VD-FECHA-SAL WS-VD-MESES WS-VD-RETORNO
           MOVE WS-VD-FECHA-SAL TO WS-NUEVA-FECHA-VCTO

      * Actualizar poliza a estado rehabilitado
           MOVE WS-NUEVA-FECHA-EFECT TO HV-NUEVA-FECHA-EFECT
           MOVE WS-NUEVA-FECHA-VCTO  TO HV-NUEVA-FECHA-VCTO

           EXEC SQL
               UPDATE POLIZAS
               SET POL_ESTADO       = 'RHB',
                   POL_FECHA_REHAB  = :HV-MOV-FECHA,
                   POL_FECHA_EFECT  = :HV-NUEVA-FECHA-EFECT,
                   POL_FECHA_VCTO   = :HV-NUEVA-FECHA-VCTO,
                   POL_MESES_VENCIDA = 0
               WHERE POL_NUMERO = :HV-POL-NUMERO
           END-EXEC

           IF SQLCODE NOT = 0
               MOVE 32 TO L-CODIGO-RETORNO
               STOP RUN
           END-IF

      * Registrar movimiento de rehabilitacion via MOVPOL
           MOVE L-POL-NUMERO   TO WS-MP-POL-NUMERO
           MOVE 'RHB'          TO WS-MP-TIPO
           MOVE L-PRIMA-REHAB  TO WS-MP-MONTO
           MOVE 'REHABILITACION POLIZA' TO WS-MP-CONCEPTO
           MOVE L-USUARIO      TO WS-MP-USUARIO
           CALL 'MOVPOL' USING WS-MOVPOL

           IF WS-MP-RETORNO NOT = 0
               MOVE 33 TO L-CODIGO-RETORNO
           END-IF

      * Registrar movimiento directo de mora acumulada
           EXEC SQL
               SELECT NEXT VALUE FOR SEQ_MOVIMIENTOS
               INTO :HV-MOV-ID
               FROM SYSIBM.SYSDUMMY1
           END-EXEC

           MOVE WS-FECHA-HOY  TO HV-MOV-FECHA
           MOVE L-PRIMA-REHAB TO HV-MOV-MONTO
           MOVE 'MORA ACUMULADA REHABILITACION' TO HV-MOV-CONCEPTO
           MOVE L-USUARIO     TO HV-MOV-USUARIO

           EXEC SQL
               INSERT INTO MOVIMIENTOS
               (MOV_ID, MOV_POL_NUMERO, MOV_TIPO,
                MOV_FECHA, MOV_MONTO, MOV_CONCEPTO,
                MOV_USUARIO, MOV_ESTADO)
               VALUES
               (:HV-MOV-ID, :HV-POL-NUMERO, 'RHB',
                :HV-MOV-FECHA, :HV-MOV-MONTO, :HV-MOV-CONCEPTO,
                :HV-MOV-USUARIO, 'A')
           END-EXEC

           STOP RUN.
