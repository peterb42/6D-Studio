#engine v8
// 6D Studio – gestapeltes Bild mit PixInsight fertig entwickeln (PixInsight 1.9.6)
//
// Aufruf (vom Studio, ohne Oberfläche):
//    PixInsight -n --automation-mode --no-focus-stealing -r=<diese Datei>,<Eingabe>,<Ausgabe ohne Endung> --force-exit
//
// Eingabe: Live-Stack- oder Siril-FITS, WBPP-Master (XISF) – linear und farbig.
// Ausgabe: <Ausgabe>.jpg (Qualität 92) und <Ausgabe>.tif (16 Bit).
//
// Schritte – nur Prozesse von PixInsight selbst, mit ihren Standardwerten:
//    GradientCorrection         Hintergrund und Gradienten (Lichtverschmutzung) entfernen
//    BackgroundNeutralization   Himmelshintergrund grau
//    ColorCalibration           Weißabgleich an den Sternen des ganzen Bilds (ohne Gaia-Datenbank, ohne Astrometrie)
//    MultiscaleAdaptiveStretch  strecken, Sterne bleiben klein, Farben etwas kräftiger
//    SCNR                       Grünstich entfernen
//
// Fortschritt für das Studio auf der Standardausgabe: „6D>> step <Schritt>“, am Ende „6D>> fertig“ oder
// „6D>> error <Meldung>“ (der Rückgabewert von PixInsight sagt darüber nichts).

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
   let input = args[ 0 ], output = args[ 1 ];

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
