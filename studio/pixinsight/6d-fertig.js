#engine v8
// 6D Studio – gestapeltes Bild mit PixInsight fertig entwickeln (PixInsight 1.9.6)
//
// Aufruf (vom Studio, ohne Oberfläche):
//    PixInsight -n --automation-mode --no-focus-stealing -r=<diese Datei>,<Eingabe>,<Ausgabe ohne Endung>[,<Modell>]
//               --force-exit
//
// Eingabe: Live-Stack- oder Siril-FITS, WBPP-Master (XISF) – linear und farbig.
// Ausgabe: <Ausgabe>.jpg (Qualität 92) und <Ausgabe>.tif (16 Bit).
//
// Schritte – nur Prozesse von PixInsight selbst, mit ihren Standardwerten:
//    GradientCorrection         Hintergrund und Gradienten (Lichtverschmutzung) entfernen
//    BackgroundNeutralization   Himmelshintergrund grau
//    ColorCalibration           Weißabgleich an den Sternen des ganzen Bilds (ohne Gaia-Datenbank, ohne Astrometrie)
//    MLDenoise                  nur mit Modell (.xmlm von Pleiades): KI-Entrauschen des linearen Bilds – auf einer
//                               NVIDIA-Karte ~19 s für 20 Megapixel, auf einem M1 ~110 s; geht es schief (z. B. zu
//                               wenig Grafikspeicher), wird ohne Entrauschen weiterentwickelt („6D>> warn …“)
//    MultiscaleAdaptiveStretch  strecken, Sterne bleiben klein, Farben etwas kräftiger
//    SCNR                       Grünstich entfernen
//
// Fortschritt für das Studio auf der Standardausgabe: „6D>> step <Schritt>“, „6D>> warn <Meldung>“, am Ende
// „6D>> fertig“ oder „6D>> error <Meldung>“ (der Rückgabewert von PixInsight sagt darüber nichts).

function sixdSay( text )
{
   cout( "\n6D>> " + text + "\n" );
   if ( typeof cflush === "function" )
      cflush();
}

function sixdStep( label )
{
   sixdSay( "step " + label );
   console.noteln( "<end><cbr><br>* 6D Studio: " + label );
}

function sixdSave( window, path, hints )
{
   if ( !window.saveAs( path, false /*queryOptions*/, false /*allowMessages*/, false /*strict*/,
                        false /*verifyOverwrite*/, hints ) )
      throw new Error( "Kann nicht speichern: " + path );
   sixdSay( "saved " + path );
}

function sixdMain()
{
   let args = Runtime.jsArguments;
   if ( args.length < 2 )
      throw new Error( "Aufruf: 6d-fertig.js,<Eingabe>,<Ausgabe ohne Endung>" );
   let input = args[ 0 ], output = args[ 1 ], model = args.length > 2 ? args[ 2 ] : "";

   sixdStep( "Bild laden" );
   let windows = ImageWindow.open( input );
   if ( windows.length < 1 )
      throw new Error( "Kann nicht öffnen: " + input );
   let window = windows[ 0 ];
   for ( let i = 1; i < windows.length; ++i )        // WBPP-Master: dazu die Maske vom Beschneiden
      windows[ i ].forceClose();
   let view = window.mainView;
   if ( view.image.numberOfChannels < 3 )
      throw new Error( "Nur farbige (debayerte) Stapel lassen sich fertig entwickeln" );

   sixdStep( "Hintergrund und Gradient entfernen" );
   ( new GradientCorrection ).executeOn( view );

   sixdStep( "Farben kalibrieren" );
   ( new BackgroundNeutralization ).executeOn( view );
   ( new ColorCalibration ).executeOn( view );

   if ( model.length > 0 )
   {
      sixdStep( "Entrauschen (MLDenoise)" );
      try
      {
         let P = new MLDenoise;
         P.modelPath = model;
         let t = Date.now();
         if ( !P.executeOn( view ) )
            throw new Error( "MLDenoise lief nicht durch" );
         sixdSay( "info Entrauschen in " + ( ( Date.now() - t ) / 1000 ).toFixed( 1 ) + " s" );
      }
      catch ( x )
      {
         sixdSay( "warn Entrauschen übersprungen: " + String( x.message ?? x ).replace( /[\r\n]+/g, " " ) );
      }
   }

   sixdStep( "Strecken" );
   ( new MultiscaleAdaptiveStretch ).executeOn( view );

   sixdStep( "Grünstich entfernen" );
   ( new SCNR ).executeOn( view );

   sixdStep( "Speichern" );
   window.setSampleFormat( 16, false );
   sixdSave( window, output + ".tif", "" );
   sixdSave( window, output + ".jpg", "quality 92" );
   window.forceClose();
   sixdSay( "fertig" );
}

try
{
   sixdMain();
}
catch ( x )
{
   sixdSay( "error " + String( x.message ?? x ).replace( /[\r\n]+/g, " " ) );
   console.criticalln( "*** 6D Studio: " + ( x.message ?? x ) );
}
