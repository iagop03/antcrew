      ******************************************
      * MOVPOL  - REGISTRO DE MOVIMIENTOS     *
      * COPY: POLREC, MOVREC                  *
      * CALL: VALDAT                          *
      * SQL:  SELECT POLIZAS,                 *
      *       INSERT MOVIMIENTOS              *
      ******************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. MOVPOL.
       AUTHOR.     SISTEMAS JARDINERIA SA.

       DATA DIVISION.
       WORKING-STORAGE SECTION.
       COPY POLREC.
       COPY MOVREC.
       01 WS-CONTROL.
           05 WS-SQLCODE-LOCAL    PIC S9(9).
           05 WS-FECHA-HOY        PIC 9(8).
           05 WS-MOV-ID-SEQ       PIC 9(12).

      * Parametros para VALDAT
       01 WS-VALDAT.
           05 WS-VD-FUNCION       PIC X(1).
           05 WS-VD-FECHA1        PIC 9(8).
           05 WS-VD-FECHA2        PIC 9(8).
           05 WS-VD-RESULTADO     PIC S9(6).
           05 WS-VD-FECHA-SAL     PIC 9(8).
           05 WS-VD-MESES         PIC S9(4).
           05 WS-VD-RETORNO       PIC 9(2).

       EXEC SQL BEGIN DECLARE SECTION END-EXEC.
       01 HV-POL-NUMERO           PIC 9(10).
       01 HV-POL-ESTADO           PIC X(3).
       01 HV-POL-FECHA-VCTO       PIC 9(8).
       01 HV-POL-PRIMA-MENSUAL    PIC 9(10)V99.
       01 HV-MOV-ID               PIC 9(12).
       01 HV-MOV-POL              PIC 9(10).
       01 HV-MOV-TIPO             PIC X(3).
       01 HV-MOV-FECHA            PIC 9(8).
       01 HV-MOV-MONTO            PIC 9(10)V99.
       01 HV-MOV-CONCEPTO         PIC X(40).
       01 HV-MOV-USUARIO          PIC X(8).
       EXEC SQL END DECLARE SECTION END-EXEC.

       LINKAGE SECTION.
       01 L-MOV-ENTRADA.
           05 L-POL-NUMERO        PIC 9(10).
           05 L-MOV-TIPO          PIC X(3).
           05 L-MOV-MONTO         PIC 9(10)V99.
           05 L-MOV-CONCEPTO      PIC X(40).
           05 L-MOV-USUARIO       PIC X(8).
           05 L-CODIGO-RETORNO    PIC 9(2).

       PROCEDURE DIVISION USING L-MOV-ENTRADA.

           MOVE 0 TO L-CODIGO-RETORNO
           MOVE L-POL-NUMERO TO HV-POL-NUMERO

      * Validar que la poliza existe y esta activa o vencida
           EXEC SQL
               SELECT POL_ESTADO, POL_FECHA_VCTO, POL_PRIMA_MENSUAL
               INTO :HV-POL-ESTADO, :HV-POL-FECHA-VCTO,
                    :HV-POL-PRIMA-MENSUAL
               FROM POLIZAS
               WHERE POL_NUMERO = :HV-POL-NUMERO
           END-EXEC

           IF SQLCODE NOT = 0
               MOVE 20 TO L-CODIGO-RETORNO
               STOP RUN
           END-IF

           IF HV-POL-ESTADO = 'CAN'
               MOVE 21 TO L-CODIGO-RETORNO
               STOP RUN
           END-IF

      * Validar fecha de movimiento
           MOVE FUNCTION CURRENT-DATE(1:8) TO WS-FECHA-HOY
           MOVE '1' TO WS-VD-FUNCION
           MOVE WS-FECHA-HOY TO WS-VD-FECHA1
           CALL 'VALDAT' USING WS-VD-FUNCION WS-VD-FECHA1
                               WS-VD-FECHA2 WS-VD-RESULTADO
                               WS-VD-FECHA-SAL WS-VD-MESES WS-VD-RETORNO
           IF WS-VD-RETORNO NOT = 0
               MOVE 22 TO L-CODIGO-RETORNO
               STOP RUN
           END-IF

      * Obtener siguiente ID de movimiento
           EXEC SQL
               SELECT NEXT VALUE FOR SEQ_MOVIMIENTOS
               INTO :HV-MOV-ID
               FROM SYSIBM.SYSDUMMY1
           END-EXEC

      * Registrar movimiento
           MOVE HV-MOV-ID        TO HV-MOV-ID
           MOVE L-POL-NUMERO     TO HV-MOV-POL
           MOVE L-MOV-TIPO       TO HV-MOV-TIPO
           MOVE WS-FECHA-HOY     TO HV-MOV-FECHA
           MOVE L-MOV-MONTO      TO HV-MOV-MONTO
           MOVE L-MOV-CONCEPTO   TO HV-MOV-CONCEPTO
           MOVE L-MOV-USUARIO    TO HV-MOV-USUARIO

           EXEC SQL
               INSERT INTO MOVIMIENTOS
               (MOV_ID, MOV_POL_NUMERO, MOV_TIPO,
                MOV_FECHA, MOV_MONTO, MOV_CONCEPTO,
                MOV_USUARIO, MOV_ESTADO)
               VALUES
               (:HV-MOV-ID, :HV-MOV-POL, :HV-MOV-TIPO,
                :HV-MOV-FECHA, :HV-MOV-MONTO, :HV-MOV-CONCEPTO,
                :HV-MOV-USUARIO, 'A')
           END-EXEC

           IF SQLCODE NOT = 0
               MOVE 23 TO L-CODIGO-RETORNO
           END-IF

           STOP RUN.
