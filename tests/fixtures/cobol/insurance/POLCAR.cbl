      ******************************************
      * POLCAR  - CARGA Y VALIDACION POLIZAS  *
      * COPY: POLREC                          *
      * CALL: CALPRM, VALDAT                  *
      * SQL:  INSERT POLIZAS, SELECT CONTRATOS*
      ******************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. POLCAR.
       AUTHOR.     SISTEMAS JARDINERIA SA.

       DATA DIVISION.
       WORKING-STORAGE SECTION.
       COPY POLREC.
       01 WS-CONTROL.
           05 WS-POLIZAS-CARGADAS PIC 9(8) VALUE ZEROS.
           05 WS-POLIZAS-ERROR    PIC 9(8) VALUE ZEROS.
           05 WS-FIN-ARCHIVO      PIC X(1) VALUE 'N'.
               88 WS-FIN                   VALUE 'S'.
           05 WS-SQLCODE-LOCAL    PIC S9(9).

      * Parametros para CALPRM
       01 WS-CALPRM.
           05 WS-CA-CONTRATO      PIC X(6).
           05 WS-CA-FECHA-NACIM   PIC 9(8).
           05 WS-CA-MONTO-ASEG    PIC 9(12)V99.
           05 WS-CA-PRIMA         PIC 9(10)V99.
           05 WS-CA-RETORNO       PIC 9(2).

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
       01 HV-POL-CONTRATO         PIC X(6).
       01 HV-POL-ASEGURADO        PIC X(30).
       01 HV-POL-FECHA-EFECT      PIC 9(8).
       01 HV-POL-FECHA-VCTO       PIC 9(8).
       01 HV-POL-MONTO-ASEG       PIC 9(12)V99.
       01 HV-POL-PRIMA-MENSUAL    PIC 9(10)V99.
       01 HV-POL-AGENTE           PIC X(6).
       01 HV-PLAZO-MESES          PIC 9(4).
       EXEC SQL END DECLARE SECTION END-EXEC.

       PROCEDURE DIVISION.

           PERFORM UNTIL WS-FIN
               READ ENTRADA-POLIZAS
                   AT END MOVE 'S' TO WS-FIN-ARCHIVO
                   NOT AT END
                       PERFORM 1000-PROCESAR-POLIZA
               END-READ
           END-PERFORM
           STOP RUN.

       1000-PROCESAR-POLIZA.
      * Validar fecha efectiva
           MOVE '1' TO WS-VD-FUNCION
           MOVE POL-FECHA-EFECT TO WS-VD-FECHA1
           CALL 'VALDAT' USING WS-VD-FUNCION WS-VD-FECHA1
                               WS-VD-FECHA2 WS-VD-RESULTADO
                               WS-VD-FECHA-SAL WS-VD-MESES WS-VD-RETORNO
           IF WS-VD-RETORNO NOT = 0
               ADD 1 TO WS-POLIZAS-ERROR
               GO TO 1000-FIN
           END-IF

      * Calcular prima
           MOVE POL-CONTRATO   TO WS-CA-CONTRATO
           MOVE POL-MONTO-ASEG TO WS-CA-MONTO-ASEG
           CALL 'CALPRM' USING WS-CALPRM
           IF WS-CA-RETORNO NOT = 0
               ADD 1 TO WS-POLIZAS-ERROR
               GO TO 1000-FIN
           END-IF
           MOVE WS-CA-PRIMA TO POL-PRIMA-MENSUAL

      * Verificar contrato activo
           MOVE POL-CONTRATO TO HV-POL-CONTRATO
           EXEC SQL
               SELECT PLAZO_MESES
               INTO :HV-PLAZO-MESES
               FROM CONTRATOS
               WHERE COD_CONTRATO = :HV-POL-CONTRATO
                 AND ESTADO = 'ACT'
           END-EXEC
           IF SQLCODE NOT = 0
               ADD 1 TO WS-POLIZAS-ERROR
               GO TO 1000-FIN
           END-IF
           MOVE HV-PLAZO-MESES TO POL-PLAZO-MESES

      * Insertar poliza
           MOVE POL-NUMERO         TO HV-POL-NUMERO
           MOVE POL-ASEGURADO      TO HV-POL-ASEGURADO
           MOVE POL-FECHA-EFECT    TO HV-POL-FECHA-EFECT
           MOVE POL-FECHA-VCTO     TO HV-POL-FECHA-VCTO
           MOVE POL-MONTO-ASEG     TO HV-POL-MONTO-ASEG
           MOVE POL-PRIMA-MENSUAL  TO HV-POL-PRIMA-MENSUAL
           MOVE POL-AGENTE         TO HV-POL-AGENTE

           EXEC SQL
               INSERT INTO POLIZAS
               (POL_NUMERO, POL_CONTRATO, POL_ASEGURADO,
                POL_FECHA_EFECT, POL_FECHA_VCTO,
                POL_MONTO_ASEG, POL_PRIMA_MENSUAL,
                POL_ESTADO, POL_AGENTE)
               VALUES
               (:HV-POL-NUMERO, :HV-POL-CONTRATO, :HV-POL-ASEGURADO,
                :HV-POL-FECHA-EFECT, :HV-POL-FECHA-VCTO,
                :HV-POL-MONTO-ASEG, :HV-POL-PRIMA-MENSUAL,
                'ACT', :HV-POL-AGENTE)
           END-EXEC

           IF SQLCODE = 0
               ADD 1 TO WS-POLIZAS-CARGADAS
           ELSE
               ADD 1 TO WS-POLIZAS-ERROR
           END-IF

       1000-FIN.
           EXIT.
