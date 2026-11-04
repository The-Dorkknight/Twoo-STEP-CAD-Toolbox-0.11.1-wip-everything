hookDrop('hello','.txt');                                   // the drop box: id d-hello; accepted file types
function runHello(){start('hello','hello',{skip_blank:$('hello-skip').checked})}    // start(tab, job kind, parameters)
