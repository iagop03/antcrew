      ******************************************
      * VALDAT  - VALIDACION Y CALCULO FECHAS *
      * Standalone utility: no COPY, no CALL  *
      * Llamado por: CALPRM, POLCAR, MOVPOL,  *
      *              REHPOL                   *
      ******************************************
       IDENTIFICATION DIVISION.
       PROGRAM-ID. VALDAT.
       AUTHOR.     SISTEMAS JARDINERIA SA.

       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-TRABAJO.
           05 WS-ANIO1            PIC 9(4).
           05 WS-MES1             PIC 9(2).
           05 WS-DIA1             PIC 9(2).
           05 WS-ANIO2            PIC 9(4).
           05 WS-MES2             PIC 9(2).
           05 WS-DIA2             PIC 9(2).
           05 WS-DELTA-ANIOS      PIC S9(4).
           05 WS-DELTA-MESES      PIC S9(6).
           05 WS-MES-NUEVO        PIC S9(4).
           05 WS-ANIO-NUEVO       PIC 9(4).

       LINKAGE SECTION.
       01 L-FUNCION               PIC X(1).
       01 L-FECHA1                PIC 9(8).
       01 L-FECHA2                PIC 9(8).
       01 L-RESULTADO-MESES       PIC S9(6).
       01 L-FECHA-SALIDA          PIC 9(8).
       01 L-MESES-SUMAR           PIC S9(4).
       01 L-CODIGO-RETORNO        PIC 9(2).

       PROCEDURE DIVISION USING L-FUNCION L-FECHA1 L-FECHA2
                                 L-RESULTADO-MESES L-FECHA-SALIDA
                                 L-MESES-SUMAR L-CODIGO-RETORNO.

           MOVE 0 TO L-CODIGO-RETORNO
           EVALUATE L-FUNCION
               WHEN '1'
                   PERFORM 1000-VALIDAR-FECHA
               WHEN '2'
                   PERFORM 2000-MESES-DIFERENCIA
               WHEN '3'
                   PERFORM 3000-SUMAR-MESES
               WHEN OTHER
                   MOVE 99 TO L-CODIGO-RETORNO
           END-EVALUATE
           STOP RUN.

       1000-VALIDAR-FECHA.
           MOVE L-FECHA1(1:4) TO WS-ANIO1
           MOVE L-FECHA1(5:2) TO WS-MES1
           MOVE L-FECHA1(7:2) TO WS-DIA1
           IF WS-MES1 < 1 OR WS-MES1 > 12
               MOVE 01 TO L-CODIGO-RETORNO
           ELSE IF WS-DIA1 < 1 OR WS-DIA1 > 31
               MOVE 02 TO L-CODIGO-RETORNO
           ELSE IF WS-ANIO1 < 1900 OR WS-ANIO1 > 2099
               MOVE 03 TO L-CODIGO-RETORNO
           END-IF.

       2000-MESES-DIFERENCIA.
           MOVE L-FECHA1(1:4) TO WS-ANIO1
           MOVE L-FECHA1(5:2) TO WS-MES1
           MOVE L-FECHA2(1:4) TO WS-ANIO2
           MOVE L-FECHA2(5:2) TO WS-MES2
           COMPUTE WS-DELTA-ANIOS = WS-ANIO2 - WS-ANIO1
           COMPUTE WS-DELTA-MESES = WS-MES2 - WS-MES1
           COMPUTE L-RESULTADO-MESES =
               (WS-DELTA-ANIOS * 12) + WS-DELTA-MESES.

       3000-SUMAR-MESES.
           MOVE L-FECHA1(1:4) TO WS-ANIO1
           MOVE L-FECHA1(5:2) TO WS-MES1
           MOVE L-FECHA1(7:2) TO WS-DIA1
           COMPUTE WS-MES-NUEVO  = WS-MES1  + L-MESES-SUMAR
           MOVE WS-ANIO1 TO WS-ANIO-NUEVO
           PERFORM UNTIL WS-MES-NUEVO <= 12
               ADD 1 TO WS-ANIO-NUEVO
               SUBTRACT 12 FROM WS-MES-NUEVO
           END-PERFORM
           PERFORM UNTIL WS-MES-NUEVO > 0
               SUBTRACT 1 FROM WS-ANIO-NUEVO
               ADD 12 TO WS-MES-NUEVO
           END-PERFORM
           MOVE WS-ANIO-NUEVO TO L-FECHA-SALIDA(1:4)
           MOVE WS-MES-NUEVO  TO L-FECHA-SALIDA(5:2)
           MOVE WS-DIA1       TO L-FECHA-SALIDA(7:2).
