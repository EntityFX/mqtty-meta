def validate_pair(base,changed):
 if base.get('queueCapacity')!=1024 or changed.get('queueCapacity')!=4096:raise ValueError('Expected queue capacities 1024 and 4096')
 if {k for k in base.keys()|changed.keys() if base.get(k)!=changed.get(k)}!={'queueCapacity'}:raise ValueError('Only queueCapacity may differ')
