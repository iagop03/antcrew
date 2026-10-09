      ******************************************
      * CALPRM  - CALCULO DE PRIMA MENSUAL    *
      * COPY: POLREC                          *
      * CALL: VALDAT                          *
      * SQL:  SELECT TARIFAS, CONTRATOS       *
      ******************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALPRM.
       AUTHOR.     SISTEMAS JARDINERIA SA.

       DATA DIVISION.
       WORKING-STORAGE SECTION.
       COPY POLREC.
       01 WS-CALCULO.
           05 WS-EDAD-ASEGURADO   PIC 9(3).
           05 WS-FACTOR-TARIFA    PIC 9(4)V9(4).
           05 WS-PRIMA-BASE       PIC 9(10)V99.
           05 WS-PRIMA-RIESGO     PIC 9(10)V99.
           05 WS-PRIMA-CALCULADA  PIC 9(10)V99.
           05 WS-FECHA-HOY        PIC 9(8).
           05 WS-FECHA-NACIM      PIC 9(8).
           05 WS-MESES-EDAD       PIC S9(6).
           05 WS-SQLCODE-LOCAL    PIC S9(9).

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
       01 HV-CONTRATO             PIC X(6).
       01 HV-MONTO-ASEG           PIC 9(12)V99.
       01 HV-EDAD                 PIC 9(3).
       01 HV-FACTOR               PIC 9(4)V9(4).
       01 HV-PRIMA-BASE           PIC 9(10)V99.
       EXEC SQL END DECLARE SECTION END-EXEC.

       LINKAGE SECTION.
       01 L-POLREC.
           05 L-POL-CONTRATO      PIC X(6).
           05 L-POL-FECHA-NACIM   PIC 9(8).
           05 L-POL-MONTO-ASEG    PIC 9(12)V99.
           05 L-PRIMA-CALCULADA   PIC 9(10)V99.
           05 L-CODIGO-RETORNO    PIC 9(2).

       PROCEDURE DIVISION USING L-POLREC.

           MOVE 0 TO L-CODIGO-RETORNO
           MOVE L-POL-CONTRATO TO HV-CONTRATO

      * Calcular edad del asegurado via VALDAT
           MOVE '2' TO WS-VD-FUNCION
           MOVE L-POL-FECHA-NACIM TO WS-VD-FECHA1
           MOVE FUNCTION CURRENT-DATE(1:8) TO WS-VD-FECHA2
           CALL 'VALDAT' USING WS-VD-FUNCION WS-VD-FECHA1
                               WS-VD-FECHA2 WS-VD-RESULTADO
                               WS-VD-FECHA-SAL WS-VD-MESES WS-VD-RETORNO
           COMPUTE WS-EDAD-ASEGURADO = WS-VD-RESULTADO / 12

      * Leer tarifa del producto
           MOVE L-POL-MONTO-ASEG TO HV-MONTO-ASEG
           MOVE WS-EDAD-ASEGURADO TO HV-EDAD

           EXEC SQL
               SELECT FACTOR, PRIMA_BASE
               INTO :HV-FACTOR, :HV-PRIMA-BASE
               FROM TARIFAS
               WHERE CONTRATO = :HV-CONTRATO
                 AND EDAD_INICIO <= :HV-EDAD
                 AND EDAD_FIN    >= :HV-EDAD
           END-EXEC

           IF SQLCODE NOT = 0
               MOVE 10 TO L-CODIGO-RETORNO
               STOP RUN
           END-IF

      * Leer condiciones del contrato
           EXEC SQL
               SELECT MONTO_MIN, MONTO_MAX
               FROM CONTRATOS
               WHERE COD_CONTRATO = :HV-CONTRATO
                 AND ESTADO = 'ACT'
           END-EXEC

           IF SQLCODE NOT = 0
               MOVE 11 TO L-CODIGO-RETORNO
               STOP RUN
           END-IF

      * Calcular prima final
           COMPUTE WS-PRIMA-BASE    = HV-PRIMA-BASE * L-POL-MONTO-ASEG
           COMPUTE WS-PRIMA-RIESGO  = WS-PRIMA-BASE * HV-FACTOR
           COMPUTE L-PRIMA-CALCULADA = WS-PRIMA-BASE + WS-PRIMA-RIESGO

           STOP RUN.
