// 6D Studio – Ereignisskript für WBPP (WeightedBatchPreprocessing, PixInsight 1.9.6)
//
// WBPP ruft dieses Skript im Automatikbetrieb bei jedem Schritt auf (usePipelineScript=true,
// pipelineScriptFile=<diese Datei>). Es schreibt eine Zeile pro Ereignis auf die Standardausgabe von
// PixInsight – daran zeigt das Studio den Fortschritt (die Konsole von PixInsight selbst sieht es nicht):
//
//    6D>> pipeline <Anzahl Schritte>
//    6D>> start <i>/<n> <Schritt> | <Bildgruppe>
//    6D>> done <i>/<n> <Schritt> | <Bildgruppe> | <Status> | <Meldung>
//    6D>> end
//
// Status wie OperationBlockStatus in BPP-OperationQueue.js: 2 = fertig, 3 = fehlgeschlagen, 4 = abgebrochen.
// Ein Fehler hier hält WBPP nicht an (WBPP meldet ihn nur als Warnung).

function sixdSay( text )
{
   cout( "\n6D>> " + text + "\n" );
   if ( typeof cflush === "function" )
      cflush();
}

function sixdGroup( env )
{
   if ( env.group == undefined )
      return "";
   let g = env.group.toShortString ? env.group.toShortString() : String( env.group );
   return " | " + String( g ).replace( /[\r\n|]+/g, " " );
}

function sixdText( value )
{
   return String( value == undefined ? "" : value ).replace( /[\r\n|]+/g, " " );
}

function onPipelineStart( env, state )
{
   sixdSay( "pipeline " + env.operationsCount );
}

function onOperationStart( env, state )
{
   sixdSay( "start " + ( env.operationIndex + 1 ) + "/" + env.operationsCount + " " + sixdText( env.name ) + sixdGroup( env ) );
}

function onOperationDone( env, state )
{
   let group = sixdGroup( env );
   sixdSay( "done " + ( env.operationIndex + 1 ) + "/" + env.operationsCount + " " + sixdText( env.name )
            + ( group.length > 0 ? group : " | " ) + " | " + env.status + " | " + sixdText( env.statusMessage ) );
}

function onPipelineEnd( env, state )
{
   sixdSay( "end" );
}
