_wormhole_completion() {
    local IFS=$'\n'
    local response

    response=$(env COMP_WORDS="${COMP_WORDS[*]}" COMP_CWORD=$COMP_CWORD _WORMHOLE_COMPLETE=bash_complete $1)

    for completion in $response; do
        IFS=',' read type value <<< "$completion"

        if [[ $type == 'dir' ]]; then
            COMPREPLY=()
            compopt -o dirnames
        elif [[ $type == 'file' ]]; then
            COMPREPLY=()
            compopt -o default
        elif [[ $type == 'plain' ]]; then
            COMPREPLY+=($value)
        fi
    done

    return 0
}

_wormhole_completion_setup() {
    if [[ "${BASH_VERSINFO[0]}" -lt 4 ]]; then
        complete -F _wormhole_completion wormhole
    else
        complete -o nosort -F _wormhole_completion wormhole
    fi
}

_wormhole_completion_setup;

